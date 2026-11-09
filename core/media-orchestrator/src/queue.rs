//! CORE-004 opt-in scheduling. Only accepted jobs enter state; preparation and
//! reservations are owned values, rolled back on every pre-acceptance failure.
use super::{JobState, heavy};
use media_domain::{
    ACCURATE_REENCODE_PROFILE, AudioSelection, BackendConfig, CoreError, MediaError, MediaRequest,
    QueueCommand, Request, STREAM_COPY_V1_PROFILE, TrimStrategy, parse_request_with_ai,
    validate_job_id,
};
use media_ffmpeg::PreparedMedia;
use serde_json::{Value, json};
use std::collections::{BTreeMap, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;
const CAPACITY: usize = 32;
const HISTORY: usize = 100;
// Serializes Core-004 and development AI admission across engines so their
// independent storage ledgers cannot both reserve based on stale snapshots.
static ADMISSION_GATE: Mutex<()> = Mutex::new(());

fn admission() -> Result<std::sync::MutexGuard<'static, ()>, CoreError> {
    ADMISSION_GATE.lock().map_err(|_| {
        heavy::quarantine();
        CoreError::Internal
    })
}

#[derive(Clone)]
enum QueueItem {
    Media(MediaRequest),
    #[cfg(target_os = "macos")]
    Ai,
}

impl QueueItem {
    fn operation(&self) -> &'static str {
        match self {
            Self::Media(request) => request.operation(),
            #[cfg(target_os = "macos")]
            Self::Ai => "ai_separate",
        }
    }
}

#[derive(Clone)]
enum QueuePlan {
    Media(Arc<PreparedMedia>),
    #[cfg(target_os = "macos")]
    Ai(Arc<super::ai::AiPlan>),
}

struct QueueJob {
    request: QueueItem,
    destination_identity: Option<std::path::PathBuf>,
    prepared: Option<QueuePlan>,
    cancel: Arc<AtomicBool>,
    state: JobState,
    result: Option<Value>,
    error: Option<MediaError>,
    summary: Value,
    sequence: u64,
}
impl QueueJob {
    fn snapshot(&self, id: &str) -> Value {
        json!({"schema_version":0,"job_id":id,"operation":self.request.operation(),"state":self.state.as_str(),"mock_only":false,"progress":null,
  "error":self.error.as_ref().map(|e|json!({"code":e.code,"message":e.message})),"result":self.result,"request_summary":self.summary,"created_sequence":self.sequence})
    }
    fn history(&self, id: &str) -> Value {
        let mut object = self.summary.as_object().cloned().unwrap_or_default();
        object.insert("job_id".into(), json!(id));
        object.insert("operation".into(), json!(self.request.operation()));
        object.insert("state".into(), json!(self.state.as_str()));
        object.insert("created_sequence".into(), json!(self.sequence));
        object.insert(
            "error".into(),
            self.error
                .as_ref()
                .map_or(Value::Null, |e| json!({"code":e.code,"message":e.message})),
        );
        Value::Object(object)
    }
}
#[derive(Clone)]
struct Submission {
    id: String,
    fingerprint: QueueCommand,
    job_ids: Vec<String>,
}
struct State {
    jobs: BTreeMap<String, QueueJob>,
    pending: VecDeque<String>,
    running: Option<String>,
    history: VecDeque<String>,
    submissions: VecDeque<Submission>,
    next: u64,
    shutdown: bool,
    cleanup_failed: bool,
}
impl State {
    fn terminal(&mut self, id: &str) {
        if self.jobs.get(id).is_some_and(|j| j.state.active()) {
            return;
        }
        self.history.retain(|old| old != id);
        self.history.push_front(id.to_owned());
        while self.history.len() > HISTORY {
            if let Some(old) = self.history.pop_back() {
                self.jobs.remove(&old);
            }
        }
    }
    fn active_count(&self) -> usize {
        self.jobs.values().filter(|j| j.state.active()).count()
    }
    fn snapshot(&self) -> Value {
        let global = media_ffmpeg::resource_snapshot();
        json!({"schema_version":0,"operation":"queue_snapshot","queued_job_ids":self.pending,"running_job_id":self.running,"active_count":self.active_count(),"queue_capacity":CAPACITY,"history_limit":HISTORY,"history":self.history.iter().filter_map(|id|self.jobs.get(id).map(|j|j.history(id))).collect::<Vec<_>>(),"resources":{"scope":"this_engine_nonterminal_plans","global_reserved_storage_bytes":global["reserved_storage_bytes"],"global_reserved_target_count":global["reserved_target_count"],"global_estimated_retained_plan_bytes":global["estimated_retained_plan_bytes"],"global_quarantined":global["quarantined"],"reserved_storage_bytes":self.jobs.values().filter(|j|j.state.active()).map(|j|j.summary["reserved_storage_bytes"].as_u64().unwrap_or(0)).sum::<u64>(),"estimated_retained_plan_bytes":self.jobs.values().filter(|j|j.state.active()).map(|j|j.summary["estimated_retained_plan_bytes"].as_u64().unwrap_or(0)).sum::<u64>(),"maximum_estimated_plan_bytes":134217728},"cleanup_unconfirmed":self.cleanup_failed||heavy::quarantined()||media_ffmpeg::quarantined()})
    }
    fn prune_submissions(&mut self) {
        while self.submissions.len() > HISTORY {
            let index = self.submissions.iter().position(|s| {
                !s.job_ids
                    .iter()
                    .any(|id| self.jobs.get(id).is_some_and(|j| j.state.active()))
            });
            if let Some(index) = index {
                self.submissions.remove(index);
            } else {
                break;
            }
        }
    }
}
type Shared = Arc<(Mutex<State>, Condvar)>;
pub(crate) struct QueueEngine {
    config: BackendConfig,
    shared: Shared,
    worker: Option<JoinHandle<()>>,
}
fn rejected(operation: &str, index: usize, code: &str, message: &str) -> Value {
    json!({"schema_version":0,"operation":operation,"accepted":false,"errors":[{"index":index,"code":code,"message":message}]})
}
fn source_name(request: &MediaRequest) -> String {
    let path = match request {
        MediaRequest::Probe { source }
        | MediaRequest::Trim { source, .. }
        | MediaRequest::Transcode { source, .. } => source,
    };
    path.file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("unavailable")
        .to_owned()
}
fn destination(request: &MediaRequest) -> Option<&std::path::Path> {
    match request {
        MediaRequest::Trim { destination, .. } | MediaRequest::Transcode { destination, .. } => {
            Some(destination)
        }
        _ => None,
    }
}
fn summary(request: &MediaRequest, prepared: &PreparedMedia) -> Value {
    let mut object = prepared.summary().as_object().cloned().unwrap_or_default();
    object.insert("source_name".into(), json!(source_name(request)));
    object.insert(
        "destination_name".into(),
        destination(request).map_or(Value::Null, |p| {
            json!(
                p.file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("unavailable")
            )
        }),
    );
    object.insert("operation".into(), json!(request.operation()));
    match request {
        MediaRequest::Probe { .. } => {
            object.insert("requested_range_us".into(), Value::Null);
            object.insert("profile".into(), Value::Null);
            object.insert("audio_selection".into(), Value::Null);
        }
        MediaRequest::Trim {
            start_us,
            end_us,
            strategy,
            ..
        } => {
            object.insert(
                "requested_range_us".into(),
                json!({"start_us":start_us,"end_us":end_us}),
            );
            object.insert(
                "profile".into(),
                match strategy {
                    TrimStrategy::StreamCopy => Value::Null,
                    TrimStrategy::StreamCopyV1 => json!(STREAM_COPY_V1_PROFILE),
                    TrimStrategy::AccurateReencode => json!(ACCURATE_REENCODE_PROFILE),
                },
            );
            object.insert("audio_selection".into(), json!({"mode":"all_supported"}));
        }
        MediaRequest::Transcode {
            range,
            profile,
            audio_selection,
            ..
        } => {
            object.insert(
                "requested_range_us".into(),
                range.map_or(
                    Value::Null,
                    |r| json!({"start_us":r.start_us,"end_us":r.end_us}),
                ),
            );
            object.insert("profile".into(), json!(profile.as_str()));
            object.insert(
                "audio_selection".into(),
                match audio_selection {
                    AudioSelection::All => json!({"mode":"all_supported"}),
                    AudioSelection::None => json!({"mode":"none"}),
                    AudioSelection::One { stream_index } => {
                        json!({"mode":"one","stream_index":stream_index})
                    }
                },
            );
        }
    }
    Value::Object(object)
}
impl QueueEngine {
    pub(crate) fn new(config: BackendConfig) -> Result<Self, CoreError> {
        let state = State {
            jobs: BTreeMap::new(),
            pending: VecDeque::new(),
            running: None,
            history: VecDeque::new(),
            submissions: VecDeque::new(),
            next: 1,
            shutdown: false,
            cleanup_failed: false,
        };
        let shared = Arc::new((Mutex::new(state), Condvar::new()));
        let worker_shared = shared.clone();
        let worker_config = config.clone();
        let worker = std::thread::Builder::new()
            .name("mediastudio-core004-worker".into())
            .spawn(move || run_worker(worker_shared, worker_config))
            .map_err(|_| CoreError::Internal)?;
        Ok(Self {
            config,
            shared,
            worker: Some(worker),
        })
    }
    pub(crate) fn submit(&mut self, input: &[u8]) -> Result<Value, CoreError> {
        bounded(self.submit_inner(input)?)
    }
    fn submit_inner(&mut self, input: &[u8]) -> Result<Value, CoreError> {
        match parse_request_with_ai(input, self.config.ai_backend.is_some())? {
            Request::Mock(_) => Err(CoreError::UnsupportedOperation),
            // Configuration/validation are present but the AI backend has not
            // yet passed integration. Never fabricate a queued job.
            Request::AiSeparate {
                source,
                destination,
            } => {
                #[cfg(target_os = "macos")]
                if self
                    .config
                    .ai_backend
                    .as_ref()
                    .and_then(|x| x.development_root.as_ref())
                    .is_some()
                {
                    return self.accept_ai(source, destination);
                }
                // Read-only rejection checks are safe to expose while the
                // actual backend remains unavailable. They never create a job
                // or establish that the path will remain unchanged later.
                match std::fs::symlink_metadata(&source) {
                    Ok(m) if m.is_file() && !m.file_type().is_symlink() && m.len() > 0 => (),
                    _ => {
                        return Ok(rejected(
                            "ai_separate",
                            0,
                            "INVALID_INPUT",
                            "The source is not an available regular file.",
                        ));
                    }
                }
                match std::fs::symlink_metadata(&destination) {
                    Ok(_) => Ok(rejected(
                        "ai_separate",
                        0,
                        "OUTPUT_EXISTS",
                        "The destination already exists.",
                    )),
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                        Err(CoreError::UnsupportedOperation)
                    }
                    Err(_) => Ok(rejected(
                        "ai_separate",
                        0,
                        "PERMISSION_DENIED",
                        "The destination cannot be inspected.",
                    )),
                }
            }
            Request::Media(request) => self.accept(vec![request], None, "submit"),
            Request::Queue(QueueCommand::Snapshot) => Ok(self
                .shared
                .0
                .lock()
                .map_err(|_| CoreError::Internal)?
                .snapshot()),
            Request::Queue(QueueCommand::Reorder { job_ids }) => {
                let mut state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
                let mut expected = state.pending.iter().cloned().collect::<Vec<_>>();
                expected.sort();
                let mut supplied = job_ids.clone();
                supplied.sort();
                if expected != supplied {
                    return Ok(rejected(
                        "queue_reorder",
                        0,
                        "INVALID_INPUT",
                        "Reordering requires exactly all currently queued job IDs.",
                    ));
                }
                state.pending = job_ids.into();
                self.shared.1.notify_one();
                Ok(
                    json!({"schema_version":0,"operation":"queue_reorder","accepted":true,"queued_job_ids":state.pending}),
                )
            }
            Request::Queue(QueueCommand::ClearHistory) => {
                let mut state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
                let ids = state.history.drain(..).collect::<Vec<_>>();
                let count = ids.len();
                for id in ids {
                    state.jobs.remove(&id);
                }
                let active = state.jobs.keys().cloned().collect::<Vec<_>>();
                state
                    .submissions
                    .retain(|s| s.job_ids.iter().any(|id| active.contains(id)));
                Ok(
                    json!({"schema_version":0,"operation":"history_clear","accepted":true,"cleared_count":count}),
                )
            }
            Request::Queue(command @ QueueCommand::BatchSubmit { .. }) => {
                let QueueCommand::BatchSubmit { items, .. } = &command else {
                    unreachable!()
                };
                self.accept(items.clone(), Some(command), "batch_submit")
            }
            Request::Queue(command @ QueueCommand::Retry { .. }) => {
                if let Some(value) = self.replay(&command, "job_retry")? {
                    return Ok(value);
                }
                let QueueCommand::Retry {
                    job_id,
                    new_destination,
                    ..
                } = &command
                else {
                    unreachable!()
                };
                let request = {
                    let state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
                    let Some(job) = state.jobs.get(job_id) else {
                        return Ok(rejected(
                            "job_retry",
                            0,
                            "NOT_FOUND",
                            "The original job is no longer retained.",
                        ));
                    };
                    if !matches!(job.state, JobState::Failed | JobState::Cancelled) {
                        return Ok(rejected(
                            "job_retry",
                            0,
                            "INVALID_INPUT",
                            "Only failed or cancelled jobs can be retried.",
                        ));
                    }
                    let mut request = match job.request.clone() {
                        QueueItem::Media(request) => request,
                        #[cfg(target_os = "macos")]
                        QueueItem::Ai => {
                            return Ok(rejected(
                                "job_retry",
                                0,
                                "INVALID_INPUT",
                                "AI retries are not supported in this development subset.",
                            ));
                        }
                    };
                    match &mut request {
                        MediaRequest::Trim { destination, .. }
                        | MediaRequest::Transcode { destination, .. } => {
                            if destination == new_destination {
                                return Ok(rejected(
                                    "job_retry",
                                    0,
                                    "INVALID_INPUT",
                                    "Retry requires a different new destination.",
                                ));
                            }
                            *destination = new_destination.clone();
                        }
                        _ => {
                            return Ok(rejected(
                                "job_retry",
                                0,
                                "INVALID_INPUT",
                                "Probe jobs do not have export retries.",
                            ));
                        }
                    };
                    request
                };
                self.accept(vec![request], Some(command), "job_retry")
            }
        }
    }
    #[cfg(target_os = "macos")]
    fn accept_ai(
        &mut self,
        source: std::path::PathBuf,
        destination: std::path::PathBuf,
    ) -> Result<Value, CoreError> {
        let _admission = admission()?;
        {
            let state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
            if state.shutdown || state.cleanup_failed || heavy::quarantined() {
                return Ok(rejected(
                    "ai_separate",
                    0,
                    "CLEANUP_FAILED",
                    "The engine cannot safely accept new work.",
                ));
            }
            if state.active_count() >= CAPACITY {
                return Ok(rejected(
                    "ai_separate",
                    0,
                    "RESOURCE_LIMIT",
                    "The queue is full.",
                ));
            }
        }
        let plan = match super::ai::AiPlan::prepare(&self.config, &source, &destination) {
            Ok(plan) => Arc::new(plan),
            Err(error) => return Ok(rejected("ai_separate", 0, error.code, &error.message)),
        };
        let mut state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
        if state.shutdown || state.cleanup_failed || heavy::quarantined() {
            return Ok(rejected(
                "ai_separate",
                0,
                "CLEANUP_FAILED",
                "Cleanup is unconfirmed.",
            ));
        }
        if state.active_count() >= CAPACITY {
            return Ok(rejected(
                "ai_separate",
                0,
                "RESOURCE_LIMIT",
                "The queue is full.",
            ));
        }
        let sequence = state.next;
        state.next = state.next.checked_add(1).ok_or(CoreError::LimitExceeded)?;
        let id = format!("media-{sequence}");
        let mut summary = plan.summary();
        if let Some(object) = summary.as_object_mut() {
            object.insert("operation".into(), json!("ai_separate"));
        }
        let job = QueueJob {
            request: QueueItem::Ai,
            destination_identity: None,
            prepared: Some(QueuePlan::Ai(plan)),
            cancel: Arc::new(AtomicBool::new(false)),
            state: JobState::Queued,
            result: None,
            error: None,
            summary,
            sequence,
        };
        let response = job.snapshot(&id);
        state.jobs.insert(id.clone(), job);
        state.pending.push_back(id);
        self.shared.1.notify_one();
        Ok(response)
    }

    fn replay(&self, command: &QueueCommand, operation: &str) -> Result<Option<Value>, CoreError> {
        let key = match command {
            QueueCommand::BatchSubmit { submission_id, .. }
            | QueueCommand::Retry { submission_id, .. } => submission_id,
            _ => return Ok(None),
        };
        let state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
        Ok(state.submissions.iter().find(|s|&s.id==key).map(|old|if &old.fingerprint==command {json!({"schema_version":0,"operation":operation,"accepted":true,"replayed":true,"submission_id":key,"job_ids":old.job_ids})}else{rejected(operation,0,"SUBMISSION_CONFLICT","This submission ID already identifies a different accepted request.")}))
    }
    fn accept(
        &mut self,
        requests: Vec<MediaRequest>,
        command: Option<QueueCommand>,
        operation: &str,
    ) -> Result<Value, CoreError> {
        let _admission = admission()?;
        #[cfg(target_os = "macos")]
        if super::ai::any_reserved() {
            return Ok(rejected(
                operation,
                0,
                "RESOURCE_LIMIT",
                "A development AI reservation is active.",
            ));
        }
        if let Some(command) = &command
            && let Some(value) = self.replay(command, operation)?
        {
            return Ok(value);
        }
        {
            let state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
            if state.shutdown
                || state.cleanup_failed
                || heavy::quarantined()
                || media_ffmpeg::quarantined()
            {
                return Ok(rejected(
                    operation,
                    0,
                    "CLEANUP_FAILED",
                    "The engine cannot safely accept new work.",
                ));
            }
            if state.active_count() + requests.len() > CAPACITY {
                return Ok(rejected(
                    operation,
                    0,
                    "RESOURCE_LIMIT",
                    "The batch exceeds the 32 nonterminal job capacity.",
                ));
            }
        }
        let retry_destination = if let Some(QueueCommand::Retry { job_id, .. }) = &command {
            let state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
            let Some(original) = state
                .jobs
                .get(job_id)
                .and_then(|job| job.destination_identity.clone())
            else {
                return Ok(rejected(
                    operation,
                    0,
                    "NOT_FOUND",
                    "The original export destination is no longer retained.",
                ));
            };
            Some(original)
        } else {
            None
        };
        let cancel = AtomicBool::new(false);
        let mut prepared = Vec::with_capacity(requests.len());
        for (index, request) in requests.iter().enumerate() {
            match media_ffmpeg::preflight(&self.config, request, &cancel) {
                Ok(plan) => prepared.push(Arc::new(plan)),
                Err(error) => return Ok(rejected(operation, index, error.code, &error.message)),
            }
        }
        if let Some(original) = retry_destination {
            let Some(plan) = prepared.first() else {
                return Err(CoreError::Internal);
            };
            match media_ffmpeg::destinations_equivalent(&original, plan.destination_identity()) {
                Ok(false) => {}
                Ok(true) => {
                    return Ok(rejected(
                        operation,
                        0,
                        "INVALID_INPUT",
                        "Retry requires a genuinely different output location.",
                    ));
                }
                Err(error) => {
                    if error.code == "CLEANUP_FAILED" {
                        plan.mark_quarantined();
                        heavy::quarantine();
                    }
                    return Ok(rejected(operation, 0, error.code, &error.message));
                }
            }
        }
        // No jobs/IDs are observable until every prepared plan and all RAII resources
        // exist. A failed preflight drops the whole local vector, releasing reservations.
        let mut state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
        if state.cleanup_failed || heavy::quarantined() || media_ffmpeg::quarantined() {
            return Ok(rejected(
                operation,
                0,
                "CLEANUP_FAILED",
                "Cleanup became unconfirmed during preflight.",
            ));
        }
        let count = u64::try_from(requests.len()).map_err(|_| CoreError::LimitExceeded)?;
        state
            .next
            .checked_add(count)
            .ok_or(CoreError::LimitExceeded)?;
        let summaries = requests
            .iter()
            .zip(&prepared)
            .map(|(r, p)| summary(r, p))
            .collect::<Vec<_>>();
        for (index, value) in summaries.iter().enumerate() {
            if serde_json::to_vec(value)
                .map_err(|_| CoreError::Internal)?
                .len()
                > 4096
            {
                return Ok(rejected(
                    operation,
                    index,
                    "RESOURCE_LIMIT",
                    "The request summary exceeds its retained bound.",
                ));
            }
        }
        let mut ids = Vec::new();
        for ((request, plan), value) in requests.into_iter().zip(prepared).zip(summaries) {
            let sequence = state.next;
            state.next += 1;
            let id = format!("media-{sequence}");
            state.jobs.insert(
                id.clone(),
                QueueJob {
                    destination_identity: destination(&request)
                        .map(|_| plan.destination_identity().to_owned()),
                    request: QueueItem::Media(request),
                    prepared: Some(QueuePlan::Media(plan)),
                    cancel: Arc::new(AtomicBool::new(false)),
                    state: JobState::Queued,
                    result: None,
                    error: None,
                    summary: value,
                    sequence,
                },
            );
            state.pending.push_back(id.clone());
            ids.push(id);
        }
        let response = if let Some(command) = command {
            let key = match &command {
                QueueCommand::BatchSubmit { submission_id, .. }
                | QueueCommand::Retry { submission_id, .. } => submission_id.clone(),
                _ => return Err(CoreError::Internal),
            };
            state.submissions.push_back(Submission {
                id: key.clone(),
                fingerprint: command,
                job_ids: ids.clone(),
            });
            state.prune_submissions();
            json!({"schema_version":0,"operation":operation,"accepted":true,"replayed":false,"submission_id":key,"job_ids":ids})
        } else {
            state
                .jobs
                .get(&ids[0])
                .ok_or(CoreError::Internal)?
                .snapshot(&ids[0])
        };
        self.shared.1.notify_one();
        Ok(response)
    }
    pub(crate) fn snapshot(&self, id: &str) -> Result<Value, CoreError> {
        validate_job_id(id)?;
        bounded(
            self.shared
                .0
                .lock()
                .map_err(|_| CoreError::Internal)?
                .jobs
                .get(id)
                .map(|j| j.snapshot(id))
                .ok_or(CoreError::NotFound)?,
        )
    }
    pub(crate) fn cancel(&self, id: &str) -> Result<(), CoreError> {
        validate_job_id(id)?;
        let mut state = self.shared.0.lock().map_err(|_| CoreError::Internal)?;
        let job = state.jobs.get_mut(id).ok_or(CoreError::NotFound)?;
        match job.state {
            JobState::Queued => {
                job.cancel.store(true, Ordering::Release);
                job.state = JobState::Cancelled;
                job.prepared = None;
                state.pending.retain(|old| old != id);
                state.terminal(id);
            }
            JobState::Running => {
                job.cancel.store(true, Ordering::Release);
                job.state = JobState::Cancelling;
            }
            _ => {}
        }
        self.shared.1.notify_one();
        Ok(())
    }
    pub(crate) fn active(&self) -> bool {
        self.shared
            .0
            .lock()
            .map_or(true, |s| s.cleanup_failed || s.active_count() != 0)
    }
}
fn run_worker(shared: Shared, config: BackendConfig) {
    loop {
        let (id, request, prepared, cancel, mut permit) = {
            let Ok(mut state) = shared.0.lock() else {
                return;
            };
            while state.pending.is_empty() && !state.shutdown {
                state = match shared.1.wait(state) {
                    Ok(s) => s,
                    Err(_) => return,
                };
            }
            if state.shutdown {
                return;
            }
            if state.cleanup_failed || heavy::quarantined() || media_ffmpeg::quarantined() {
                state.cleanup_failed = true;
                let ids = state.pending.drain(..).collect::<Vec<_>>();
                for id in ids {
                    if let Some(job) = state.jobs.get_mut(&id) {
                        job.state = JobState::Failed;
                        job.error = Some(MediaError::new(
                            "CLEANUP_FAILED",
                            "A process-wide media resource remains unconfirmed.",
                        ));
                        job.prepared = None;
                    }
                    state.terminal(&id);
                }
                continue;
            }
            let Some(first) = state.pending.front() else {
                continue;
            };
            let heavy_request = state.jobs.get(first).is_some_and(|j| {
                !matches!(j.request, QueueItem::Media(MediaRequest::Probe { .. }))
            });
            let permit = if heavy_request {
                match heavy::try_acquire() {
                    Ok(Some(p)) => Some(p),
                    Ok(None) => {
                        drop(state);
                        std::thread::sleep(Duration::from_millis(10));
                        continue;
                    }
                    Err(error) => {
                        state.cleanup_failed = true;
                        let ids = state.pending.drain(..).collect::<Vec<_>>();
                        for id in ids {
                            if let Some(job) = state.jobs.get_mut(&id) {
                                job.state = JobState::Failed;
                                job.error = Some(error.clone());
                                job.prepared = None;
                            }
                            state.terminal(&id);
                        }
                        continue;
                    }
                }
            } else {
                None
            };
            // The permit was acquired while holding the pending-order lock. Reorder can
            // change any queued job while waiting, and the worker now claims the real head.
            let Some(id) = state.pending.pop_front() else {
                continue;
            };
            let Some(job) = state.jobs.get_mut(&id) else {
                continue;
            };
            if job.state != JobState::Queued {
                continue;
            }
            let Some(prepared) = job.prepared.clone() else {
                job.state = JobState::Failed;
                job.error = Some(MediaError::new("INTERNAL", "Prepared plan is missing."));
                state.terminal(&id);
                continue;
            };
            job.state = JobState::Running;
            let request = job.request.clone();
            let cancel = job.cancel.clone();
            state.running = Some(id.clone());
            (id, request, prepared, cancel, permit)
        };
        #[cfg(target_os = "macos")]
        if let QueuePlan::Ai(plan) = &prepared {
            run_ai_job(&shared, &id, plan, &cancel, &mut permit);
            drop(prepared);
            drop(permit);
            continue;
        }
        let outcome =
            match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| match &prepared {
                QueuePlan::Media(plan) => media_ffmpeg::execute_prepared(&config, plan, &cancel),
                #[cfg(target_os = "macos")]
                QueuePlan::Ai(_) => unreachable!("handled above"),
            })) {
                Ok(v) => v,
                Err(payload) => {
                    std::mem::forget(payload);
                    match &prepared {
                        QueuePlan::Media(plan) => plan.mark_quarantined(),
                        #[cfg(target_os = "macos")]
                        QueuePlan::Ai(plan) => plan.mark_quarantined(),
                    }
                    Err(MediaError::new(
                        "CLEANUP_FAILED",
                        "The worker unwound without confirmed media cleanup.",
                    ))
                }
            };
        let _ = request;
        if outcome
            .as_ref()
            .err()
            .is_some_and(|e| e.code == "CLEANUP_FAILED")
        {
            heavy::quarantine();
            if let Some(permit) = &mut permit {
                permit.quarantine();
            }
        }
        if let Ok(mut state) = shared.0.lock() {
            if let Some(job) = state.jobs.get_mut(&id) {
                match outcome {
                    Ok(value) => {
                        job.state = JobState::Succeeded;
                        job.result = Some(value);
                    }
                    Err(error) => {
                        job.state = if error.code == "CANCELLED" {
                            JobState::Cancelled
                        } else {
                            JobState::Failed
                        };
                        if error.code == "CLEANUP_FAILED" {
                            state.cleanup_failed = true;
                        }
                        if let Some(job) = state.jobs.get_mut(&id) {
                            job.error = Some(error);
                        }
                    }
                }
                if let Some(job) = state.jobs.get_mut(&id) {
                    job.prepared = None;
                }
            }
            state.running = None;
            state.terminal(&id);
            state.prune_submissions();
            shared.1.notify_all();
        }
        drop(prepared);
        drop(permit);
    }
}

#[cfg(target_os = "macos")]
fn quarantine_ai(plan: &super::ai::AiPlan, permit: &mut Option<heavy::Permit>) {
    plan.mark_quarantined();
    heavy::quarantine();
    if let Some(permit) = permit {
        permit.quarantine();
    }
}

#[cfg(target_os = "macos")]
fn run_ai_job(
    shared: &Shared,
    id: &str,
    plan: &Arc<super::ai::AiPlan>,
    cancel: &AtomicBool,
    permit: &mut Option<heavy::Permit>,
) {
    let run = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| plan.run(cancel)));
    let outcome: Result<Value, MediaError> = match run {
        Ok(Ok(mut ready)) => {
            // This lock is the cancellation/commit linearization boundary.
            // No worker wait, decoding or long validation occurs while held.
            let Ok(mut state) = shared.0.lock() else {
                quarantine_ai(plan, permit);
                drop(ready); // its transaction owner quarantines, never deletes blindly
                return;
            };
            if cancel.load(Ordering::Acquire) {
                drop(state);
                match ready.cleanup() {
                    Ok(()) => Err(MediaError::new("CANCELLED", "AI operation was cancelled.")),
                    Err(error) => Err(error),
                }
            } else {
                let commit =
                    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| ready.commit()));
                match commit {
                    Ok(Ok((result, cleanup_unconfirmed))) => {
                        if cleanup_unconfirmed {
                            quarantine_ai(plan, permit);
                            state.cleanup_failed = true;
                        }
                        if let Some(job) = state.jobs.get_mut(id) {
                            job.state = JobState::Succeeded;
                            job.result = Some(result);
                            job.prepared = None;
                        }
                        state.running = None;
                        state.terminal(id);
                        state.prune_submissions();
                        shared.1.notify_all();
                        return;
                    }
                    Ok(Err(error)) => {
                        drop(state);
                        match ready.cleanup() {
                            Ok(()) => Err(error),
                            Err(cleanup) => Err(cleanup),
                        }
                    }
                    Err(payload) => {
                        std::mem::forget(payload);
                        drop(state);
                        quarantine_ai(plan, permit);
                        drop(ready);
                        Err(MediaError::new(
                            "CLEANUP_FAILED",
                            "AI commit did not finish safely.",
                        ))
                    }
                }
            }
        }
        Ok(Err(error)) => Err(error),
        Err(payload) => {
            std::mem::forget(payload);
            quarantine_ai(plan, permit);
            Err(MediaError::new(
                "CLEANUP_FAILED",
                "AI execution unwound without confirmed cleanup.",
            ))
        }
    };
    if outcome
        .as_ref()
        .err()
        .is_some_and(|error| error.code == "CLEANUP_FAILED")
    {
        quarantine_ai(plan, permit);
    }
    if let Ok(mut state) = shared.0.lock() {
        if outcome
            .as_ref()
            .err()
            .is_some_and(|error| error.code == "CLEANUP_FAILED")
        {
            state.cleanup_failed = true;
        }
        if let Some(job) = state.jobs.get_mut(id) {
            if let Err(error) = outcome {
                job.state = if error.code == "CANCELLED" {
                    JobState::Cancelled
                } else {
                    JobState::Failed
                };
                job.error = Some(error);
            }
            job.prepared = None;
        }
        state.running = None;
        state.terminal(id);
        state.prune_submissions();
        shared.1.notify_all();
    } else {
        quarantine_ai(plan, permit);
    }
}
impl Drop for QueueEngine {
    fn drop(&mut self) {
        if let Ok(mut state) = self.shared.0.lock() {
            state.shutdown = true;
            for job in state.jobs.values() {
                job.cancel.store(true, Ordering::Release);
            }
            self.shared.1.notify_all();
        }
        if let Some(worker) = self.worker.take()
            && let Err(payload) = worker.join()
        {
            std::mem::forget(payload);
        }
    }
}

fn bounded(value: Value) -> Result<Value, CoreError> {
    if serde_json::to_vec(&value)
        .map_err(|_| CoreError::Internal)?
        .len()
        > 1024 * 1024
    {
        return Err(CoreError::LimitExceeded);
    }
    Ok(value)
}

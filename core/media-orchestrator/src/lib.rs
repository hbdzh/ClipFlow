#![forbid(unsafe_code)]

//! A bounded job engine shared by the CLI and FFI. Mock jobs remain poll-driven;
//! explicitly configured media jobs run on one serial worker per engine.

mod heavy;
mod queue;
use std::collections::BTreeMap;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::thread::JoinHandle;

use media_domain::{
    BackendConfig, CoreError, MediaError, MediaRequest, MockOutcome, Request, parse_config,
    parse_mock_request, parse_request, validate_job_id,
};
use serde_json::{Value, json};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum JobState {
    Queued,
    Running,
    Cancelling,
    Cancelled,
    Succeeded,
    Failed,
}

impl JobState {
    fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Running => "running",
            Self::Cancelling => "cancelling",
            Self::Cancelled => "cancelled",
            Self::Succeeded => "succeeded",
            Self::Failed => "failed",
        }
    }

    fn active(self) -> bool {
        matches!(self, Self::Queued | Self::Running | Self::Cancelling)
    }
}

struct MockJob {
    state: JobState,
    outcome: MockOutcome,
    cancellation_observed: bool,
}

impl MockJob {
    fn snapshot(&self, id: &str) -> Value {
        let error = (self.state == JobState::Failed).then(
            || json!({"code": "MOCK_FAILED", "message": "The mock job was configured to fail."}),
        );
        let result = (self.state == JobState::Succeeded)
            .then(|| json!({"mock_only": true, "file_created": false}));
        json!({
            "schema_version": 0,
            "job_id": id,
            "operation": "mock",
            "state": self.state.as_str(),
            "mock_only": true,
            "progress": null,
            "error": error,
            "result": result
        })
    }

    fn poll(&mut self) {
        self.state = match self.state {
            JobState::Queued => JobState::Running,
            JobState::Running => match self.outcome {
                MockOutcome::Succeed => JobState::Succeeded,
                MockOutcome::Fail => JobState::Failed,
            },
            JobState::Cancelling if !self.cancellation_observed => {
                self.cancellation_observed = true;
                JobState::Cancelling
            }
            JobState::Cancelling => JobState::Cancelled,
            terminal => terminal,
        };
    }
}

type Executor = Arc<
    dyn Fn(&BackendConfig, &MediaRequest, &AtomicBool) -> Result<Value, MediaError> + Send + Sync,
>;

struct MediaState {
    operation: &'static str,
    state: JobState,
    result: Option<Value>,
    error: Option<MediaError>,
}

impl MediaState {
    fn snapshot(&self, id: &str) -> Value {
        let error = self
            .error
            .as_ref()
            .map(|error| json!({"code":error.code,"message":error.message}));
        json!({"schema_version":0,"job_id":id,"operation":self.operation,
            "state":self.state.as_str(),"mock_only":false,"progress":null,
            "error":error,"result":self.result})
    }
}

struct SharedMedia {
    state: Mutex<MediaState>,
    cancel: AtomicBool,
}

enum Job {
    Mock(MockJob),
    Media(Arc<SharedMedia>),
}

struct Work {
    config: BackendConfig,
    request: MediaRequest,
    shared: Arc<SharedMedia>,
}

struct Scheduler {
    sender: mpsc::SyncSender<Work>,
    shutdown: Arc<AtomicBool>,
    worker: JoinHandle<()>,
}

fn run_worker(
    receiver: mpsc::Receiver<Work>,
    shutdown: Arc<AtomicBool>,
    executor: Executor,
    real_executor: bool,
) {
    while !shutdown.load(Ordering::Acquire) {
        let work = match receiver.recv() {
            Ok(work) => work,
            Err(_) => break,
        };
        if shutdown.load(Ordering::Acquire) {
            break;
        }
        if real_executor && heavy::quarantined() {
            if let Ok(mut state) = work.shared.state.lock() {
                state.state = JobState::Failed;
                state.error = Some(MediaError::new(
                    "CLEANUP_FAILED",
                    "A process-wide media resource is quarantined.",
                ));
            }
            continue;
        }
        let mut heavy_permit = None;
        if real_executor && !matches!(work.request, MediaRequest::Probe { .. }) {
            loop {
                if work.shared.cancel.load(Ordering::Acquire) || shutdown.load(Ordering::Acquire) {
                    break;
                }
                match heavy::try_acquire() {
                    Ok(Some(permit)) => {
                        heavy_permit = Some(permit);
                        break;
                    }
                    Ok(None) => std::thread::sleep(std::time::Duration::from_millis(10)),
                    Err(error) => {
                        if let Ok(mut state) = work.shared.state.lock() {
                            state.state = JobState::Failed;
                            state.error = Some(error);
                        }
                        break;
                    }
                }
            }
        }
        {
            let Ok(mut state) = work.shared.state.lock() else {
                continue;
            };
            if !matches!(state.state, JobState::Queued) {
                continue;
            }
            if work.shared.cancel.load(Ordering::Acquire) {
                state.state = JobState::Cancelled;
                continue;
            }
            state.state = JobState::Running;
        }
        // No engine borrow or caller pointer enters this thread. Backend owns its
        // subprocess and staging guards; returning CANCELLED means cleanup finished.
        let outcome = match catch_unwind(AssertUnwindSafe(|| {
            executor(&work.config, &work.request, &work.shared.cancel)
        })) {
            Ok(result) => result,
            Err(payload) => {
                std::mem::forget(payload);
                if real_executor {
                    heavy::quarantine();
                    if let Some(permit) = &mut heavy_permit {
                        permit.quarantine();
                    }
                    Err(MediaError::new(
                        "CLEANUP_FAILED",
                        "The media worker unwound without confirmed cleanup.",
                    ))
                } else {
                    Err(MediaError::new(
                        "INTERNAL",
                        "The controlled test executor failed internally.",
                    ))
                }
            }
        };
        if outcome
            .as_ref()
            .err()
            .is_some_and(|e| e.code == "CLEANUP_FAILED")
        {
            heavy::quarantine();
            if let Some(permit) = &mut heavy_permit {
                permit.quarantine();
            }
        }
        if let Ok(mut state) = work.shared.state.lock() {
            match outcome {
                // Backend validates its <=128KiB summary BEFORE publishing files.
                // A cancellation arriving after that commit must not hide success.
                Ok(value) => {
                    state.state = JobState::Succeeded;
                    state.result = Some(value);
                    state.error = None;
                }
                Err(error) => {
                    state.state = if error.code == "CANCELLED" {
                        JobState::Cancelled
                    } else {
                        JobState::Failed
                    };
                    state.error = Some(error);
                    state.result = None;
                }
            }
        }
    }
}

pub struct Engine {
    max_jobs: usize,
    next_id: u64,
    jobs: BTreeMap<String, Job>,
    media_backend: Option<BackendConfig>,
    scheduler: Option<Scheduler>,
    executor: Executor,
    queue: Option<queue::QueueEngine>,
    real_executor: bool,
}

impl Engine {
    pub fn new(config: &[u8]) -> Result<Self, CoreError> {
        Self::configured(config, Arc::new(media_ffmpeg::execute), true)
    }

    #[cfg(test)]
    fn with_executor(config: &[u8], executor: Executor) -> Result<Self, CoreError> {
        Self::configured(config, executor, false)
    }
    fn configured(
        config: &[u8],
        executor: Executor,
        real_executor: bool,
    ) -> Result<Self, CoreError> {
        let config = parse_config(config)?;
        let queue = config
            .media_backend
            .as_ref()
            .filter(|c| c.core004_enabled)
            .map(|c| queue::QueueEngine::new(c.clone()))
            .transpose()?;
        Ok(Self {
            queue,
            real_executor,
            max_jobs: config.max_jobs,
            next_id: 1,
            jobs: BTreeMap::new(),
            media_backend: config.media_backend,
            scheduler: None,
            executor,
        })
    }

    pub fn version(&self) -> Value {
        json!({"schema_version":0,"core_version":env!("CARGO_PKG_VERSION"),"abi_version":0,
            "stage":if self.queue.is_some(){"CORE-004"}else if self.media_backend.is_some(){"CORE-002"}else{"CORE-001"},
            "mock_only":self.media_backend.is_none()})
    }

    pub fn capabilities(&self) -> Value {
        match &self.media_backend {
            Some(config) => media_ffmpeg::backend_capabilities(config),
            None => media_ffmpeg::capabilities(),
        }
    }

    fn ensure_scheduler(&mut self) -> Result<(), CoreError> {
        if self.scheduler.is_some() {
            return Ok(());
        }
        let (sender, receiver) = mpsc::sync_channel(self.max_jobs);
        let shutdown = Arc::new(AtomicBool::new(false));
        let thread_shutdown = shutdown.clone();
        let executor = self.executor.clone();
        let real_executor = self.real_executor;
        let worker = std::thread::Builder::new()
            .name("mediastudio-media-worker".into())
            .spawn(move || run_worker(receiver, thread_shutdown, executor, real_executor))
            .map_err(|_| CoreError::Internal)?;
        self.scheduler = Some(Scheduler {
            sender,
            shutdown,
            worker,
        });
        Ok(())
    }

    pub fn submit(&mut self, request: &[u8]) -> Result<Value, CoreError> {
        if let Some(queue) = &mut self.queue {
            return queue.submit(request);
        }
        // Preserve the exact CORE-001 rejection for media-shaped inputs when the
        // host did not explicitly opt in, even if their media fields are absent.
        let request = if self.media_backend.is_none() {
            Request::Mock(parse_mock_request(request)?)
        } else {
            parse_request(request)?
        };
        if matches!(request, Request::Media(_)) && heavy::quarantined() {
            return Err(CoreError::Internal);
        }
        if matches!(
            request,
            Request::Queue(_) | Request::Media(MediaRequest::Transcode { .. })
        ) {
            return Err(CoreError::UnsupportedOperation);
        }
        if self.jobs.len() >= self.max_jobs {
            return Err(CoreError::LimitExceeded);
        }
        let next = self
            .next_id
            .checked_add(1)
            .ok_or(CoreError::LimitExceeded)?;
        let id = format!(
            "{}-{}",
            if matches!(request, Request::Mock(_)) {
                "mock"
            } else {
                "media"
            },
            self.next_id
        );
        let (job, response) = match request {
            Request::Queue(_) => return Err(CoreError::UnsupportedOperation),
            Request::Mock(outcome) => {
                let job = MockJob {
                    state: JobState::Queued,
                    outcome,
                    cancellation_observed: false,
                };
                let response = job.snapshot(&id);
                (Job::Mock(job), response)
            }
            Request::Media(request) => {
                let config = self
                    .media_backend
                    .clone()
                    .ok_or(CoreError::UnsupportedOperation)?;
                self.ensure_scheduler()?;
                let state = MediaState {
                    operation: request.operation(),
                    state: JobState::Queued,
                    result: None,
                    error: None,
                };
                let response = state.snapshot(&id);
                let shared = Arc::new(SharedMedia {
                    state: Mutex::new(state),
                    cancel: AtomicBool::new(false),
                });
                let work = Work {
                    config,
                    request,
                    shared: shared.clone(),
                };
                let scheduler = self.scheduler.as_ref().ok_or(CoreError::Internal)?;
                scheduler
                    .sender
                    .try_send(work)
                    .map_err(|error| match error {
                        mpsc::TrySendError::Full(_) => CoreError::LimitExceeded,
                        mpsc::TrySendError::Disconnected(_) => CoreError::Internal,
                    })?;
                (Job::Media(shared), response)
            }
        };
        self.jobs.insert(id, job);
        self.next_id = next;
        Ok(response)
    }

    pub fn snapshot(&mut self, id: &str) -> Result<Value, CoreError> {
        if let Some(queue) = &self.queue {
            return queue.snapshot(id);
        }
        validate_job_id(id)?;
        match self.jobs.get_mut(id).ok_or(CoreError::NotFound)? {
            Job::Mock(job) => {
                job.poll();
                Ok(job.snapshot(id))
            }
            Job::Media(shared) => Ok(shared
                .state
                .lock()
                .map_err(|_| CoreError::Internal)?
                .snapshot(id)),
        }
    }

    pub fn cancel(&mut self, id: &str) -> Result<(), CoreError> {
        if let Some(queue) = &self.queue {
            return queue.cancel(id);
        }
        validate_job_id(id)?;
        match self.jobs.get_mut(id).ok_or(CoreError::NotFound)? {
            Job::Mock(job) => {
                if matches!(job.state, JobState::Queued | JobState::Running) {
                    job.state = JobState::Cancelling;
                    job.cancellation_observed = false;
                }
            }
            Job::Media(shared) => {
                let mut state = shared.state.lock().map_err(|_| CoreError::Internal)?;
                match state.state {
                    // Worker has not claimed it, so no media resources exist.
                    JobState::Queued => {
                        shared.cancel.store(true, Ordering::Release);
                        state.state = JobState::Cancelled;
                    }
                    JobState::Running => {
                        shared.cancel.store(true, Ordering::Release);
                        state.state = JobState::Cancelling;
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    pub fn has_active_jobs(&self) -> bool {
        if let Some(queue) = &self.queue {
            return queue.active();
        }
        self.jobs.values().any(|job| match job {
            Job::Mock(job) => job.state.active(),
            // A poisoned status cannot safely establish quiescence.
            Job::Media(shared) => shared.state.lock().map_or(true, |state| {
                state.state.active()
                    || state
                        .error
                        .as_ref()
                        .is_some_and(|e| e.code == "CLEANUP_FAILED")
            }),
        })
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        for job in self.jobs.values() {
            if let Job::Media(shared) = job {
                shared.cancel.store(true, Ordering::Release);
            }
        }
        if let Some(scheduler) = self.scheduler.take() {
            scheduler.shutdown.store(true, Ordering::Release);
            drop(scheduler.sender); // Wake a receiver that owns no active process.
            if let Err(payload) = scheduler.worker.join() {
                std::mem::forget(payload);
            }
        }
    }
}

#[cfg(test)]
mod media_tests;

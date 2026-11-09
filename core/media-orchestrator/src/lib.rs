#![forbid(unsafe_code)]

//! A bounded, deterministic poll-driven mock engine, not an asynchronous media
//! worker. The CLI and FFI share this single state machine.

use std::collections::BTreeMap;

use media_domain::{CoreError, MockOutcome, parse_config, parse_mock_request, validate_job_id};
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

struct Job {
    state: JobState,
    outcome: MockOutcome,
    cancellation_observed: bool,
}

impl Job {
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

pub struct Engine {
    max_jobs: usize,
    next_id: u64,
    jobs: BTreeMap<String, Job>,
}

impl Engine {
    pub fn new(config: &[u8]) -> Result<Self, CoreError> {
        let config = parse_config(config)?;
        Ok(Self {
            max_jobs: config.max_jobs,
            next_id: 1,
            jobs: BTreeMap::new(),
        })
    }

    pub fn version(&self) -> Value {
        json!({
            "schema_version": 0,
            "core_version": env!("CARGO_PKG_VERSION"),
            "abi_version": 0,
            "stage": "CORE-001",
            "mock_only": true
        })
    }

    pub fn capabilities(&self) -> Value {
        media_ffmpeg::capabilities()
    }

    pub fn submit(&mut self, request: &[u8]) -> Result<Value, CoreError> {
        let outcome = parse_mock_request(request)?;
        if self.jobs.len() >= self.max_jobs {
            return Err(CoreError::LimitExceeded);
        }
        let next = self
            .next_id
            .checked_add(1)
            .ok_or(CoreError::LimitExceeded)?;
        let id = format!("mock-{}", self.next_id);
        let job = Job {
            state: JobState::Queued,
            outcome,
            cancellation_observed: false,
        };
        let response = job.snapshot(&id);
        self.jobs.insert(id, job);
        self.next_id = next;
        Ok(response)
    }

    pub fn snapshot(&mut self, id: &str) -> Result<Value, CoreError> {
        validate_job_id(id)?;
        let job = self.jobs.get_mut(id).ok_or(CoreError::NotFound)?;
        job.poll();
        Ok(job.snapshot(id))
    }

    pub fn cancel(&mut self, id: &str) -> Result<(), CoreError> {
        validate_job_id(id)?;
        let job = self.jobs.get_mut(id).ok_or(CoreError::NotFound)?;
        if matches!(job.state, JobState::Queued | JobState::Running) {
            job.state = JobState::Cancelling;
            job.cancellation_observed = false;
        }
        Ok(())
    }

    pub fn has_active_jobs(&self) -> bool {
        self.jobs.values().any(|job| job.state.active())
    }
}

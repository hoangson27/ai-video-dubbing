use std::{
    io,
    process::{Child, Command, ExitStatus},
};

pub struct EngineProcess {
    pub child: Child,
    #[cfg(windows)]
    job: Option<std::os::windows::io::OwnedHandle>,
}

impl EngineProcess {
    pub fn spawn(command: &mut Command) -> io::Result<Self> {
        #[cfg(windows)]
        return windows::spawn(command);

        #[cfg(not(windows))]
        Ok(Self { child: command.spawn()? })
    }

    pub fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        let status = self.child.try_wait()?;
        if status.is_some() {
            #[cfg(windows)]
            drop(self.job.take()); // Close inherited process pipes if the root exited first.
        }
        Ok(status)
    }

    pub fn stop(&mut self) {
        #[cfg(windows)]
        drop(self.job.take()); // KILL_ON_JOB_CLOSE stops every associated process.
        let _ = self.child.kill();
    }
}

impl Drop for EngineProcess {
    fn drop(&mut self) {
        self.stop();
        let _ = self.child.wait();
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::{
        mem::size_of,
        os::windows::{
            io::{AsRawHandle, FromRawHandle, OwnedHandle},
            process::CommandExt,
        },
        ptr::null,
    };
    use windows_sys::Win32::{
        Foundation::INVALID_HANDLE_VALUE,
        System::{
            Diagnostics::ToolHelp::{
                CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First,
                Thread32Next,
            },
            JobObjects::{
                AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
                SetInformationJobObject,
            },
            Threading::{
                CREATE_NO_WINDOW, CREATE_SUSPENDED, OpenThread, ResumeThread, THREAD_SUSPEND_RESUME,
            },
        },
    };

    pub fn spawn(command: &mut Command) -> io::Result<EngineProcess> {
        let handle = unsafe { CreateJobObjectW(null(), null()) };
        if handle.is_null() {
            return Err(io::Error::last_os_error());
        }
        // CreateJobObjectW returns a new non-inheritable handle on success.
        let job = unsafe { OwnedHandle::from_raw_handle(handle) };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            return Err(io::Error::last_os_error());
        }

        // Python's Windows launcher and venv redirector can spawn before reading stdin.
        // Suspend the initial thread until the process is in the kill-on-close job.
        let child = command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED).spawn()?;
        let process = EngineProcess { child, job: Some(job) };
        if unsafe { AssignProcessToJobObject(handle, process.child.as_raw_handle()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        resume_initial_thread(process.child.id())?;
        Ok(process)
    }

    fn resume_initial_thread(pid: u32) -> io::Result<()> {
        let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
        if snapshot == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let snapshot = unsafe { OwnedHandle::from_raw_handle(snapshot) };
        let mut entry = THREADENTRY32 { dwSize: size_of::<THREADENTRY32>() as u32, ..Default::default() };
        let mut found = unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) };
        while found != 0 {
            if entry.th32OwnerProcessID == pid {
                let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID) };
                if thread.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let thread = unsafe { OwnedHandle::from_raw_handle(thread) };
                if unsafe { ResumeThread(thread.as_raw_handle()) } == u32::MAX {
                    return Err(io::Error::last_os_error());
                }
                return Ok(());
            }
            entry.dwSize = size_of::<THREADENTRY32>() as u32;
            found = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) };
        }
        Err(io::Error::new(io::ErrorKind::NotFound, "Không tìm thấy luồng khởi tạo của Python"))
    }
}

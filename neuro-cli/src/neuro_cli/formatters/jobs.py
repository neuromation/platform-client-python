import abc
import datetime
import itertools
import sys
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Iterable, List, Optional, Tuple, Type

import humanize
from rich import box
from rich.console import Console, ConsoleRenderable, RenderableType, RenderHook
from rich.control import Control
from rich.live_render import LiveRender
from rich.markup import escape as rich_escape
from rich.styled import Styled
from rich.table import Table
from rich.text import Text, TextType

from neuro_sdk import JobDescription, JobRestartPolicy, JobStatus, JobTelemetry

from neuro_cli.parse_utils import JobColumnInfo
from neuro_cli.utils import format_size

from .utils import ImageFormatter, URIFormatter, image_formatter, no, yes

COLORS = {
    JobStatus.PENDING: "cyan",
    JobStatus.SUSPENDED: "magenta",
    JobStatus.RUNNING: "blue",
    JobStatus.SUCCEEDED: "green",
    JobStatus.CANCELLED: "yellow",
    JobStatus.FAILED: "red",
    JobStatus.UNKNOWN: "bright_red",
}


if sys.platform == "win32":
    SPINNER = itertools.cycle(r"-\|/")
else:
    SPINNER = itertools.cycle("◢◣◤◥")


def fmt_status(status: JobStatus) -> Text:
    color = COLORS.get(status, "none")
    return Text(status.value, style=color)


def format_timedelta(delta: datetime.timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 0:
        raise ValueError(f"Invalid delta {delta}: expect non-negative total value")
    _sec_in_minute = 60
    _sec_in_hour = _sec_in_minute * 60
    _sec_in_day = _sec_in_hour * 24
    d, s = divmod(s, _sec_in_day)
    h, s = divmod(s, _sec_in_hour)
    m, s = divmod(s, _sec_in_minute)
    return "".join(
        [
            f"{d}d" if d else "",
            f"{h}h" if h else "",
            f"{m}m" if m else "",
            f"{s}s" if s else "",
        ]
    )


class JobStatusFormatter:
    def __init__(self, uri_formatter: URIFormatter) -> None:
        self._format_uri = uri_formatter
        self._format_image = image_formatter(uri_formatter=uri_formatter)

    def __call__(self, job_status: JobDescription) -> RenderableType:
        assert job_status.history is not None

        table = Table(box=None, show_header=False, show_edge=False)
        table.add_column()
        table.add_column(style="bold")
        table.add_row("Job", job_status.id)
        if job_status.name:
            table.add_row("Name", job_status.name)
        if job_status.tags:
            text = ", ".join(job_status.tags)
            table.add_row("Tags", text)
        table.add_row("Owner", job_status.owner or "")
        table.add_row("Cluster", job_status.cluster_name)
        if job_status.description:
            table.add_row("Description", job_status.description)
        status_text = fmt_status(job_status.status)
        if job_status.history.reason and job_status.status in [
            JobStatus.FAILED,
            JobStatus.PENDING,
        ]:
            status_text = Text.assemble(status_text, f" ({job_status.history.reason})")
        table.add_row("Status", status_text)
        table.add_row("Image", self._format_image(job_status.container.image))

        if job_status.container.entrypoint:
            table.add_row("Entrypoint", job_status.container.entrypoint)
        if job_status.container.command:
            table.add_row("Command", job_status.container.command)
        if job_status.container.working_dir:
            table.add_row("Working dir", job_status.container.working_dir)
        if job_status.preset_name:
            table.add_row("Preset", job_status.preset_name)

        resources = Table(box=None, show_header=False, show_edge=False)
        resources.add_column()
        resources.add_column(style="bold", justify="right")
        resources.add_row(
            "Memory", format_size(job_status.container.resources.memory_mb * 1024 ** 2)
        )
        resources.add_row("CPU", f"{job_status.container.resources.cpu:0.1f}")
        if job_status.container.resources.gpu:
            resources.add_row(
                "GPU",
                f"{job_status.container.resources.gpu:0.1f} x "
                f"{job_status.container.resources.gpu_model}",
            )

        if job_status.container.resources.tpu_type:
            resources.add_row(
                "TPU",
                f"{job_status.container.resources.tpu_type}/"
                "{job_status.container.resources.tpu_software_version}",
            )

        if job_status.container.resources.shm:
            resources.add_row("Extended SHM space", "True")

        table.add_row("Resources", Styled(resources, style="reset"))

        if job_status.scheduler_enabled:
            table.add_row("Round Robin", "True")
        if job_status.preemptible_node:
            table.add_row("Preemptible Node", "True")
        if job_status.restart_policy != JobRestartPolicy.NEVER:
            table.add_row("Restart policy", job_status.restart_policy.value)
        if job_status.history.restarts != 0:
            table.add_row("Restarts", str(job_status.history.restarts))
        if job_status.life_span is not None:
            table.add_row("Life span", format_life_span(job_status.life_span))

        table.add_row("TTY", str(job_status.container.tty))

        if job_status.container.volumes:
            volumes = Table(box=None, show_header=False, show_edge=False)
            volumes.add_column("")
            volumes.add_column("")
            volumes.add_column("")
            for volume in job_status.container.volumes:
                volumes.add_row(
                    volume.container_path,
                    self._format_uri(volume.storage_uri),
                    "READONLY" if volume.read_only else " ",
                )
            table.add_row("Volumes", Styled(volumes, style="reset"))

        if job_status.container.secret_files:
            secret_files = Table(box=None, show_header=False, show_edge=False)
            secret_files.add_column("")
            secret_files.add_column("")
            for secret_file in job_status.container.secret_files:
                secret_files.add_row(
                    secret_file.container_path, self._format_uri(secret_file.secret_uri)
                )
            table.add_row("Secret files", Styled(secret_files, style="reset"))

        if job_status.container.disk_volumes:
            disk_volumes = Table(box=None, show_header=False, show_edge=False)
            disk_volumes.add_column("")
            disk_volumes.add_column("")
            disk_volumes.add_column("")
            for disk_volume in job_status.container.disk_volumes:
                disk_volumes.add_row(
                    disk_volume.container_path,
                    self._format_uri(disk_volume.disk_uri),
                    "READONLY" if disk_volume.read_only else " ",
                )
            table.add_row("Disk volumes", Styled(disk_volumes, style="reset"))

        if job_status.internal_hostname:
            table.add_row("Internal Hostname", job_status.internal_hostname)
        if job_status.internal_hostname_named:
            table.add_row("Internal Hostname Named", job_status.internal_hostname_named)
        if job_status.http_url:
            table.add_row("Http URL", str(job_status.http_url))
        if job_status.container.http:
            table.add_row("Http port", str(job_status.container.http.port))
            table.add_row(
                "Http authentication", str(job_status.container.http.requires_auth)
            )
        if job_status.container.env:
            environment = Table(box=None, show_header=False, show_edge=False)
            environment.add_column("")
            environment.add_column("")
            for key, value in job_status.container.env.items():
                environment.add_row(key, value)
            table.add_row("Environment", Styled(environment, style="reset"))
        if job_status.container.secret_env:
            secret_env = Table(box=None, show_header=False, show_edge=False)
            secret_env.add_column("")
            secret_env.add_column("")
            for key, uri in job_status.container.secret_env.items():
                secret_env.add_row(key, self._format_uri(uri))
            table.add_row("Secret environment", Styled(secret_env, style="reset"))

        assert job_status.history.created_at is not None
        table.add_row("Created", job_status.history.created_at.isoformat())
        if job_status.status in [
            JobStatus.RUNNING,
            JobStatus.SUSPENDED,
            JobStatus.FAILED,
            JobStatus.SUCCEEDED,
            JobStatus.CANCELLED,
        ]:
            assert job_status.history.started_at is not None
            table.add_row("Started", job_status.history.started_at.isoformat())
        if job_status.status in [
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.SUCCEEDED,
        ]:
            assert job_status.history.finished_at is not None
            table.add_row("Finished", job_status.history.finished_at.isoformat())
            table.add_row("Exit code", str(job_status.history.exit_code))
        if job_status.status == JobStatus.FAILED and job_status.history.description:
            table.add_row("Description", job_status.history.description)
        return table


def format_life_span(life_span: Optional[float]) -> str:
    if life_span is None:
        return ""
    if life_span == 0:
        return "no limit"
    return format_timedelta(datetime.timedelta(seconds=life_span))


def format_datetime(when: Optional[datetime.datetime]) -> str:
    if when is None:
        return ""
    assert when.tzinfo is not None
    delta = datetime.datetime.now(datetime.timezone.utc) - when
    if delta < datetime.timedelta(days=1):
        return humanize.naturaltime(delta)
    else:
        return humanize.naturaldate(when.astimezone())


class JobTelemetryFormatter(RenderHook):
    def __init__(
        self, console: Console, tz: Optional[datetime.timezone] = None
    ) -> None:
        self._tz = tz
        self._console = console
        self._live_render = LiveRender(Table.grid())

    def _format_timestamp(self, timestamp: float) -> str:
        # NOTE: ctime returns time wrt timezone
        dt = datetime.datetime.fromtimestamp(timestamp, tz=self._tz)
        return dt.ctime()

    def update(self, info: JobTelemetry) -> None:
        timestamp = self._format_timestamp(info.timestamp)
        table = Table(box=box.SIMPLE_HEAVY)
        table.add_column("TIMESTAMP", justify="right", width=24)
        table.add_column("CPU", justify="right", width=15)
        table.add_column("MEMORY (MB)", justify="right", width=15)
        table.add_column("GPU (%)", justify="right", width=15)
        table.add_column("GPU_MEMORY (MB)", justify="right", width=15)

        cpu = f"{info.cpu:.3f}"
        mem = f"{info.memory:.3f}"
        gpu = f"{info.gpu_duty_cycle}" if info.gpu_duty_cycle else "0"
        gpu_mem = f"{info.gpu_memory:.3f}" if info.gpu_memory else "0"
        table.add_row(timestamp, cpu, mem, gpu, gpu_mem)
        if self._console.is_terminal:
            self._live_render.set_renderable(table)
            with self._console:
                self._console.print(Control(""))
        else:
            self._console.print(table)

    def process_renderables(
        self, renderables: List[ConsoleRenderable]
    ) -> List[ConsoleRenderable]:
        """Process renderables to restore cursor and display progress."""
        if self._console.is_terminal:
            renderables = [
                self._live_render.position_cursor(),
                *renderables,
                self._live_render,
            ]
        return renderables

    def __enter__(self) -> "JobTelemetryFormatter":
        self._console.show_cursor(False)
        self._console.push_render_hook(self)
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> None:
        self._console.line()
        self._console.show_cursor(True)
        self._console.pop_render_hook()


class BaseJobsFormatter:
    @abc.abstractmethod
    def __call__(self, jobs: Iterable[JobDescription]) -> RenderableType:
        pass


class SimpleJobsFormatter(BaseJobsFormatter):
    def __call__(self, jobs: Iterable[JobDescription]) -> RenderableType:
        table = Table.grid()
        table.add_column("")
        for job in jobs:
            table.add_row(job.id)
        return table


@dataclass(frozen=True)
class TabularJobRow:
    id: str
    name: str
    tags: str
    status: Text
    when: str
    created: str
    started: str
    finished: str
    image: str
    owner: str
    description: str
    cluster_name: str
    command: str
    life_span: str
    workdir: str

    @classmethod
    def from_job(
        cls, job: JobDescription, username: str, image_formatter: ImageFormatter
    ) -> "TabularJobRow":
        if job.status == JobStatus.PENDING:
            when = job.history.created_at
        elif job.status == JobStatus.RUNNING:
            when = job.history.started_at
        else:
            when = job.history.finished_at
        assert when is not None
        return cls(
            id=job.id,
            name=job.name if job.name else "",
            tags=",".join(job.tags),
            status=fmt_status(job.status),
            when=format_datetime(when),
            created=format_datetime(job.history.created_at),
            started=format_datetime(job.history.started_at),
            finished=format_datetime(job.history.finished_at),
            image=image_formatter(job.container.image),
            owner=("YOU" if job.owner == username else job.owner),
            description=job.description if job.description else "",
            cluster_name=job.cluster_name,
            command=job.container.command if job.container.command else "",
            life_span=format_life_span(job.life_span),
            workdir=job.container.working_dir or "",
        )

    def to_list(self, columns: List[JobColumnInfo]) -> List[TextType]:
        return [getattr(self, column.id) for column in columns]


class TabularJobsFormatter(BaseJobsFormatter):
    def __init__(
        self,
        username: str,
        columns: List[JobColumnInfo],
        image_formatter: ImageFormatter,
    ) -> None:
        self._username = username
        self._columns = columns
        self._image_formatter = image_formatter

    def __call__(self, jobs: Iterable[JobDescription]) -> RenderableType:
        table = Table(box=box.SIMPLE_HEAVY)
        column = self._columns[0]
        table.add_column(
            column.title,
            style="bold",
            justify=column.justify,
            width=column.width,
            min_width=column.min_width,
            max_width=column.max_width,
        )
        for column in self._columns[1:]:
            table.add_column(
                column.title,
                justify=column.justify,
                width=column.width,
                min_width=column.min_width,
                max_width=column.max_width,
            )

        for job in jobs:
            table.add_row(
                *TabularJobRow.from_job(
                    job, self._username, image_formatter=self._image_formatter
                ).to_list(self._columns)
            )
        return table


class JobStartProgress:
    time_factory = staticmethod(time.monotonic)

    @classmethod
    def create(cls, console: Console, quiet: bool) -> "JobStartProgress":
        if quiet:
            return JobStartProgress()
        elif console.is_terminal:
            return DetailedJobStartProgress(console)
        return StreamJobStartProgress(console)

    def begin(self, job: JobDescription) -> None:
        # Quiet mode
        print(job.id)

    def step(self, job: JobDescription) -> None:
        pass

    def end(self, job: JobDescription) -> None:
        pass

    def _get_status_reason_message(self, job: JobDescription) -> str:
        if job.history.reason:
            return job.history.reason
        elif job.status == JobStatus.PENDING:
            return "Initializing"
        return ""

    def _get_status_description_message(self, job: JobDescription) -> str:
        description = job.history.description or ""
        if description:
            return f"({description})"
        return ""

    def __enter__(self) -> "JobStartProgress":
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> None:
        pass


class DetailedJobStartProgress(JobStartProgress, RenderHook):
    def __init__(self, console: Console) -> None:
        self._time = self.time_factory()
        self._prev = Text("")
        self._console = console
        self._spinner = SPINNER
        self._live_render = LiveRender(Text())

    def begin(self, job: JobDescription) -> None:
        self._console.print(
            f"{yes()} [b]Job ID[/b]: {rich_escape(job.id)}", markup=True
        )
        if job.name:
            self._console.print(
                f"{yes()} [b]Name[/b]: {rich_escape(job.name)}", markup=True
            )

    def step(self, job: JobDescription) -> None:
        new_time = self.time_factory()
        dt = new_time - self._time
        if job.status == JobStatus.PENDING:
            msg = Text("-", "yellow")
        elif job.status == JobStatus.FAILED:
            msg = Text("×", "red")
        else:
            # RUNNING or SUCCEDED
            msg = Text("√", "green")

        msg = Text.assemble(msg, " Status: ", fmt_status(job.status))
        reason = self._get_status_reason_message(job)
        if reason:
            msg = Text.assemble(msg, " ", (reason, "b"))
        description = self._get_status_description_message(job)
        if description:
            msg = Text.assemble(msg, " " + description)

        if msg != self._prev:
            if self._prev:
                self._console.print(self._prev)
            self._prev = msg
        else:
            msg = Text.assemble(msg, f" {next(self._spinner)} [{dt:.1f} sec]")

        self._live_render.set_renderable(msg)
        with self._console:
            self._console.print(Control(""))

    def end(self, job: JobDescription) -> None:
        out = []

        if job.status != JobStatus.FAILED:
            http_url = job.http_url
            if http_url:
                out.append(f"{yes()} [b]Http URL[/b]: {rich_escape(str(http_url))}")
            if job.life_span:
                limit = humanize.naturaldelta(datetime.timedelta(seconds=job.life_span))
                out.append(
                    f"{yes()} [yellow]The job will die in {limit}.[/yellow] "
                    "See --life-span option documentation for details.",
                )
            self._console.print("\n".join(out), markup=True)

    def process_renderables(
        self, renderables: List[ConsoleRenderable]
    ) -> List[ConsoleRenderable]:
        """Process renderables to restore cursor and display progress."""
        if self._console.is_terminal:
            renderables = [
                self._live_render.position_cursor(),
                *renderables,
                self._live_render,
            ]
        return renderables

    def __enter__(self) -> "JobStartProgress":
        self._console.show_cursor(False)
        self._console.push_render_hook(self)
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> None:
        self._console.line()
        self._console.show_cursor(True)
        self._console.pop_render_hook()


class StreamJobStartProgress(JobStartProgress):
    def __init__(self, console: Console) -> None:
        self._console = console
        self._prev = ""

    def begin(self, job: JobDescription) -> None:
        self._console.print(f"Job ID: {job.id}")
        if job.name:
            self._console.print(f"Name: {job.name}")

    def step(self, job: JobDescription) -> None:
        msg = f"Status: {job.status}"
        reason = self._get_status_reason_message(job)
        if reason:
            msg += " " + reason
        description = self._get_status_description_message(job)
        if description:
            msg += " " + description

        if job.status != JobStatus.PENDING:
            msg += "\n"

        if msg != self._prev:
            self._console.print(msg)
            self._prev = msg

    def end(self, job: JobDescription) -> None:
        pass


class JobStopProgress:
    TIMEOUT = 15 * 60
    time_factory = staticmethod(time.monotonic)

    @classmethod
    def create(cls, console: Console, quiet: bool) -> "JobStopProgress":
        if quiet:
            return JobStopProgress()
        elif console.is_terminal:
            return DetailedJobStopProgress(console)
        return StreamJobStopProgress(console)

    def __init__(self) -> None:
        self._time = self.time_factory()

    def kill(self, job: JobDescription) -> None:
        pass

    def detach(self, job: JobDescription) -> None:
        pass

    def step(self, job: JobDescription) -> bool:
        # return False if timeout, True otherwise
        new_time = self.time_factory()
        if new_time - self._time > self.TIMEOUT:
            self.timeout(job)
            return False
        else:
            self.tick(job)
            return True

    def tick(self, job: JobDescription) -> None:
        pass

    def timeout(self, job: JobDescription) -> None:
        pass

    def __enter__(self) -> "JobStopProgress":
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> None:
        pass


class DetailedJobStopProgress(JobStopProgress, RenderHook):
    def __init__(self, console: Console) -> None:
        super().__init__()
        self._console = console
        self._spinner = SPINNER
        self._live_render = LiveRender(Text())

    def _hint(self, hints: Iterable[Tuple[str, str]]) -> None:
        for title, hint in hints:
            self._console.print("title:", style="dim yellow")
            self._console.print(f"  {hint}", style="dim")

    def detach(self, job: JobDescription) -> None:
        self._console.line()
        self._console.print(
            f"{no()} [red]Terminal was detached but job is still running", markup=True
        )
        self._hint(
            [
                ("Re-attach to job", f"neuro attach {job.id}"),
                ("Check job status", f"neuro status {job.id}"),
                ("Kill job", f"neuro attach {job.id}"),
                ("Fetch job logs", f"neuro logs {job.id}"),
            ]
        )

    def kill(self, job: JobDescription) -> None:
        self._console.line()
        self._console.print(f"{no()} [red]Job was killed", markup=True)
        self._hint(
            [
                ("Get job status", f"neuro status {job.id}"),
                ("Fetch job logs", f"neuro logs {job.id}"),
            ]
        )

    def tick(self, job: JobDescription) -> None:
        new_time = self.time_factory()
        dt = new_time - self._time

        if job.status == JobStatus.RUNNING:
            msg = (
                "[yellow]-[/yellow]"
                + f" Wait for stop {next(self._spinner)} [{dt:.1f} sec]"
            )
        else:
            msg = yes() + " Stopped"

        self._live_render.set_renderable(Text.from_markup(msg))
        with self._console:
            self._console.print(Control(""))

    def timeout(self, job: JobDescription) -> None:
        self._console.line()
        self._console.print("[red]× Warning !!!", markup=True)
        self._console.print(
            f"{no()} [red]"
            "The attached session was disconnected but the job is still alive.",
            markup=True,
        )
        self._hint(
            [
                ("Reconnect to the job", f"neuro attach {job.id}"),
                ("Terminate the job", f"neuro kill {job.id}"),
            ]
        )

    def process_renderables(
        self, renderables: List[ConsoleRenderable]
    ) -> List[ConsoleRenderable]:
        """Process renderables to restore cursor and display progress."""
        if self._console.is_terminal:
            renderables = [
                self._live_render.position_cursor(),
                *renderables,
                self._live_render,
            ]
        return renderables

    def __enter__(self) -> "JobStopProgress":
        self._console.show_cursor(False)
        self._console.push_render_hook(self)
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> None:
        self._console.line()
        self._console.show_cursor(True)
        self._console.pop_render_hook()


class StreamJobStopProgress(JobStopProgress):
    def __init__(self, console: Console) -> None:
        super().__init__()
        self._console = console
        self._console.print("Wait for stopping")

    def detach(self, job: JobDescription) -> None:
        pass

    def kill(self, job: JobDescription) -> None:
        self._console.print("Job was killed")

    def tick(self, job: JobDescription) -> None:
        pass

    def timeout(self, job: JobDescription) -> None:
        self._console.print("")
        self._console.print("Warning !!!")
        self._console.print(
            "The attached session was disconnected but the job is still alive."
        )


class ExecStopProgress:
    TIMEOUT = 15
    time_factory = staticmethod(time.monotonic)

    @classmethod
    def create(cls, console: Console, quiet: bool) -> "ExecStopProgress":
        if quiet:
            return ExecStopProgress()
        elif console.is_terminal:
            return DetailedExecStopProgress(console)
        return StreamExecStopProgress(console)

    def __init__(self) -> None:
        self._time = self.time_factory()

    def __call__(self, running: bool) -> bool:
        # return False if timeout, True otherwise
        new_time = self.time_factory()
        if new_time - self._time > self.TIMEOUT:
            self.timeout()
            return False
        else:
            self.tick(running)
            return True

    def tick(self, running: bool) -> None:
        pass

    def timeout(self) -> None:
        pass

    def __enter__(self) -> "ExecStopProgress":
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> None:
        pass


class DetailedExecStopProgress(ExecStopProgress, RenderHook):
    def __init__(self, console: Console) -> None:
        super().__init__()
        self._console = console
        self._spinner = SPINNER
        self._live_render = LiveRender(Text())

    def tick(self, running: bool) -> None:
        new_time = self.time_factory()
        dt = new_time - self._time

        if running:
            msg = (
                "[yellow]-[/yellow]"
                + f"Wait for stopping {next(self._spinner)} [{dt:.1f} sec]"
            )
        else:
            msg = yes() + " Stopped"

        self._live_render.set_renderable(Text.from_markup(msg))
        with self._console:
            self._console.print(Control(""))

    def timeout(self) -> None:
        self._console.line()
        self._console.print(f"{no()} [red]Warning !!!", markup=True)
        self._console.print(
            f"{no()} [red]The attached session was disconnected "
            "but the exec process is still alive.",
            markup=True,
        )

    def process_renderables(
        self, renderables: List[ConsoleRenderable]
    ) -> List[ConsoleRenderable]:
        """Process renderables to restore cursor and display progress."""
        if self._console.is_terminal:
            renderables = [
                self._live_render.position_cursor(),
                *renderables,
                self._live_render,
            ]
        return renderables

    def __enter__(self) -> "ExecStopProgress":
        self._console.show_cursor(False)
        self._console.push_render_hook(self)
        return self

    def __exit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType,
    ) -> None:
        self._console.line()
        self._console.show_cursor(True)
        self._console.pop_render_hook()


class StreamExecStopProgress(ExecStopProgress):
    def __init__(self, console: Console) -> None:
        super().__init__()
        self._console = console
        self._console.print("Wait for stopping")

    def tick(self, running: bool) -> None:
        pass

    def timeout(self) -> None:
        self._console.print()
        self._console.print("Warning !!!")
        self._console.print(
            "The attached session was disconnected "
            "but the exec process is still alive."
        )

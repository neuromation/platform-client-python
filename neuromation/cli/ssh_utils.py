import asyncio

import aiohttp

from neuromation.api import Client, JobDescription


def _validate_args_for_ssh_session(
    container_user: str, container_key: str, jump_host_key: str
) -> None:
    # Temporal solution - pending custom Jump Server with JWT support
    if not container_user:
        raise ValueError("Specify container user name")
    if not container_key:
        raise ValueError("Specify container RSA key path.")
    if not jump_host_key:
        raise ValueError(
            "Configure Github RSA key path." "See for more info `neuro config`."
        )


def _validate_job_status_for_ssh_session(job_status: JobDescription) -> None:
    if job_status.status == "running":
        if job_status.ssh_server:
            pass
        else:
            raise ValueError("Job should be started with SSH support.")
    else:
        raise ValueError(f"Job is not running. Job status is {job_status.status}")


async def _start_ssh_tunnel(
    job_status: JobDescription,
    jump_host: str,
    jump_user: str,
    jump_key: str,
    local_port: int,
) -> None:
    _validate_job_status_for_ssh_session(job_status)
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-i",
        jump_key,
        f"{jump_user}@{jump_host}",
        "-f",
        "-N",
        "-L",
        f"{local_port}:{job_status.id}:22",
    )
    await proc.wait()


async def _connect_ssh(
    username: str,
    job_status: JobDescription,
    jump_host_key: str,
    container_user: str,
    container_key: str,
) -> None:
    _validate_job_status_for_ssh_session(job_status)
    # We shall make an attempt to connect only in case it has SSH
    ssh_hostname = job_status.jump_host()
    if not ssh_hostname:
        raise RuntimeError("Job has no SSH server enabled")
    nc_command = f"nc {job_status.id} 22"
    proxy_command = (
        f"ProxyCommand=ssh -i {jump_host_key} {username}@{ssh_hostname} {nc_command}"
    )
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-o",
        proxy_command,
        "-i",
        container_key,
        f"{container_user}@{job_status.id}",
    )
    await proc.wait()


async def connect_ssh(
    client: Client,
    job_id: str,
    jump_host_key: str,
    container_user: str,
    container_key: str,
) -> None:
    _validate_args_for_ssh_session(container_user, container_key, jump_host_key)
    # Check if job is running
    try:
        job_status = await client.jobs.status(job_id)
    except aiohttp.ClientError as e:
        raise ValueError(f"Job not found. Job Id = {job_id}") from e
    await _connect_ssh(
        client.username, job_status, jump_host_key, container_user, container_key
    )


async def remote_debug(
    client: Client, job_id: str, jump_host_key: str, local_port: int
) -> None:
    if not jump_host_key:
        raise ValueError(
            "Configure Github RSA key path." "See for more info `neuro config`."
        )
    try:
        job_status = await client.jobs.status(job_id)
    except aiohttp.ClientError as e:
        raise ValueError(f"Job not found. Job Id = {job_id}") from e
    ssh_hostname = job_status.jump_host()
    if not ssh_hostname:
        raise RuntimeError("Job has no SSH server enabled")
    await _start_ssh_tunnel(
        job_status, ssh_hostname, client.username, jump_host_key, local_port
    )
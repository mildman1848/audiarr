# LSIO / Servarr image analysis notes

## Radarr and Sonarr LSIO images

Observed from LinuxServer.io docs and public `linuxserver/docker-radarr` / `linuxserver/docker-sonarr` references:

- Standard env: `PUID`, `PGID`, `TZ`.
- Persistent config is mounted at `/config`.
- Media/download folders are mounted as separate optional volumes in docs, but a single `/data` mount is preferred for TRaSH-style homelab layouts.
- Containers run under LSIO base images with s6 overlay.
- Runtime troubleshooting pattern: `docker logs`, `docker exec` shell.

Audiarr adopts `/config`, `/data`, `PUID`, `PGID`, `TZ`, `UMASK`, s6 v3, and root init followed by `s6-setuidgid abc`.

## Listenarr

Public Listenarr docs and local repo references show an audiobook-focused app, but not a native LSIO-style layout:

- Typical config path is `/app/config` rather than `/config`.
- Port is commonly `4545`.
- It has useful audiobook-specific ideas but has had practical rough edges around metadata matching, retained hardlinks, and import semantics in our homelab notes.

Audiarr should learn from Listenarr's domain model but not copy its storage/import pitfalls. The MVP therefore starts with explicit provider/connection abstractions and conservative conversion delegation.

# Servarr parity targets

Audiarr models the settings categories from Radarr/Sonarr now, but implementation is phased.

| Area | MVP | Target |
|---|---|---|
| Host | modeled | reverse proxy URL base, SSL flags, API key auth |
| Auth | modeled only | forms/API key enforcement |
| Media management | modeled | rename, organize, hardlink/copy/import decisions |
| Quality profiles | modeled | format/bitrate scoring for audiobooks |
| Root folders | modeled | scan, free-space, permissions, identity tracking |
| Download clients | modeled | SABnzbd/qBittorrent clients |
| Indexers | modeled | Prowlarr/Newznab/Torznab |
| Connect | modeled | webhooks, Audiobookshelf scan, notifications |
| Metadata | stub chain | real Audible auth/client, Audnexus fallback |
| UI | dashboard | full CRUD settings + library views |
| Backups | modeled | automatic config DB backups |
| Updates | modeled | release check, no auto-update by default |

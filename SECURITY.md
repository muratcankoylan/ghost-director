# Local security boundary

Ghost Director is a loopback application for one trusted local operator. It is not an authenticated multiuser internet service. Do not expose its command endpoints through a public tunnel without adding and reviewing authentication, authorization, storage isolation and deployment controls.

Credentials stay in ignored server-side configuration. Keep live calls disabled until intended. Scene and provider payloads are validated, immutable media is hash-checked, and export/import rejects unsafe paths and omitted provenance. Those safeguards do not make arbitrary downloaded Blender files safe to execute or external context trustworthy.

Report security concerns privately to the repository maintainer using GitHub's private vulnerability reporting if available. Do not attach credentials or private production data to a public issue.

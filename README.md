# gns3api-py

Python wrapper to interact with the GNS3 REST API. Tested on GNS3 3.0.6.

GNS3 3.x controllers expose a `/v3`-prefixed REST API secured with JWT
bearer tokens (`POST /v3/access/users/login`) — a different scheme from the
GNS3 v2 API (HTTP Basic Auth, `/v2` prefix) that older clients such as
[`gns3fy`](https://github.com/davidban77/gns3fy) target and are incompatible
with a v3 controller. This library talks to the v3 API directly, based on
the server's own OpenAPI spec (`/openapi.json`).

## Install

```bash
pip install git+https://github.com/manoedinata/gns3api-py.git
```

## Usage

```python
from gns3api import Gns3Client

client = Gns3Client(base_url="http://<gns3-host>:80", username="...", password="...")

client.version()
projects = client.list_projects()
project = client.find_project("my-project")

client.open_project(project["project_id"])
nodes = client.list_nodes(project["project_id"])
client.start_all_nodes(project["project_id"])
```

Authentication is lazy: the first API call triggers a login and caches the
bearer token, and a `401` response transparently re-authenticates and
retries once.

## Coverage

`Gns3Client` covers projects, nodes, links and templates (list/get/create/
update/delete, start/stop/suspend/reload, plus project open/close). See
[`gns3api/client.py`](gns3api/client.py) for the full method list, or the
server's `/docs` and `/openapi.json` for the complete API surface.

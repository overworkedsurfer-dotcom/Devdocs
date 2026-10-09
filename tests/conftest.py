"""A fake devdocs.io: a small catalog and two docs, served over a mock transport."""

from __future__ import annotations

import copy
from collections import Counter

import httpx2
import pytest

from devdocs_mcp.store import DevDocsStore, Settings

MANIFEST_URL = "https://devdocs.test/docs.json"
DOCUMENTS_URL = "https://documents.devdocs.test"

CATALOG = [
    {
        "name": "JavaScript",
        "slug": "javascript",
        "type": "mdn",
        "mtime": 1700000000,
        "db_size": 2048,
        "alias": "js",
    },
    {
        "name": "Python",
        "slug": "python~3.12",
        "type": "python",
        "version": "3.12",
        "release": "3.12.1",
        "mtime": 1700000001,
        "db_size": 4096,
        "alias": "py",
    },
    {
        "name": "Python",
        "slug": "python~3.11",
        "type": "python",
        "version": "3.11",
        "release": "3.11.7",
        "mtime": 1700000002,
        "db_size": 4000,
        "alias": "py",
    },
    {
        "name": "Python",
        "slug": "python~2.7",
        "type": "python",
        "version": "2.7",
        "release": "2.7.18",
        "mtime": 1600000000,
        "db_size": 3000,
        "alias": "py",
    },
    {
        "name": "Ruby on Rails",
        "slug": "rails~7.1",
        "type": "rdoc",
        "version": "7.1",
        "mtime": 1700000003,
        "db_size": 5000,
        "alias": "ror",
    },
]

PYTHON_INDEX = {
    "entries": [
        {"name": "print()", "path": "library/functions#print", "type": "Built-in Functions"},
        {"name": "json", "path": "library/json", "type": "json"},
        {"name": "json.dumps()", "path": "library/json#json.dumps", "type": "json"},
        {"name": "os", "path": "library/os", "type": "os"},
        {"name": "os.chdir()", "path": "library/os#os.chdir", "type": "os"},
        {"name": "os.getcwd()", "path": "library/os#os.getcwd", "type": "os"},
        {"name": "os.getcwdb()", "path": "library/os#os.getcwdb", "type": "os"},
        {
            "name": "Files and Directories (os)",
            "path": "library/os#files-and-directories",
            "type": "os",
        },
        {"name": "os.path", "path": "library/os.path", "type": "os.path"},
        {"name": "os.path.join()", "path": "library/os.path#os.path.join", "type": "os.path"},
    ],
    "types": [
        {"name": "Built-in Functions", "count": 1, "slug": "built-in-functions"},
        {"name": "json", "count": 2, "slug": "json"},
        {"name": "os", "count": 5, "slug": "os"},
        {"name": "os.path", "count": 2, "slug": "os-path"},
    ],
}

PYTHON_PAGES = {
    "index": "<h1>Python 3.12 documentation</h1><p>Welcome. See <a href='library/os'>os</a>.</p>",
    "library/functions": """
<h1>Built-in Functions</h1>
<dl class="py function">
<dt class="sig sig-object py" id="print"><span class="sig-name">print</span>(<em>*objects</em>, <em>sep=' '</em>)</dt>
<dd><p>Print <em>objects</em> to the text stream <em>file</em>.</p></dd>
</dl>
""",
    "library/json": """
<h1>json — JSON encoder and decoder</h1>
<p>The json module serializes Python objects.</p>
<dl class="py function">
<dt class="sig sig-object py" id="json.dumps"><span class="sig-prename">json.</span><span class="sig-name">dumps</span>(<em>obj</em>)</dt>
<dd><p>Serialize <em>obj</em> to a JSON formatted <code>str</code>.</p>
<pre data-language="python">json.dumps({"a": 1})</pre>
<img src="data:image/png;base64,AAAA" alt="diagram">
</dd>
</dl>
""",
    "library/os": """
<h1>os — Miscellaneous operating system interfaces</h1>
<p>This module provides a portable way of using operating system dependent functionality.</p>
<h2 id="files-and-directories">Files and Directories</h2>
<p>Functions for working with files and directories.</p>
<dl class="py function">
<dt class="sig sig-object py" id="os.chdir"><span class="sig-prename">os.</span><span class="sig-name">chdir</span>(<em>path</em>)</dt>
<dd><p>Change the current working directory to <em>path</em>. See <a href="os.path#os.path.join">os.path.join()</a>.</p></dd>
</dl>
<dl class="py function">
<dt class="sig sig-object py" id="os.getcwd"><span class="sig-prename">os.</span><span class="sig-name">getcwd</span>()</dt>
<dd><p>Return a string representing the current working directory.</p></dd>
<dt class="sig sig-object py" id="os.getcwdb"><span class="sig-prename">os.</span><span class="sig-name">getcwdb</span>()</dt>
<dd><p>Return a bytestring representing the current working directory.</p></dd>
</dl>
<h2 id="process-parameters">Process Parameters</h2>
<p>These functions provide information about the current process. See <a href="#os.getcwd">getcwd</a>
and <a href="https://peps.python.org/pep-0008/">PEP 8</a>.</p>
""",
    "library/os.path": """
<h1>os.path — Common pathname manipulations</h1>
<dl class="py function">
<dt class="sig sig-object py" id="os.path.join"><span class="sig-prename">os.path.</span><span class="sig-name">join</span>(<em>path</em>, <em>*paths</em>)</dt>
<dd><p>Join one or more path segments intelligently. Compare <a href="../library/os#os.getcwd">os.getcwd()</a>.</p></dd>
</dl>
""",
}

JAVASCRIPT_INDEX = {
    "entries": [
        {"name": "Array", "path": "global_objects/array", "type": "Array"},
        {
            "name": "Array.prototype.flatMap()",
            "path": "global_objects/array/flatmap",
            "type": "Array",
        },
        {"name": "Array.prototype.map()", "path": "global_objects/array/map", "type": "Array"},
    ],
    "types": [{"name": "Array", "count": 3, "slug": "array"}],
}

JAVASCRIPT_PAGES = {
    "global_objects/array": "<h1>Array</h1><p>The <code>Array</code> object stores lists.</p>",
    "global_objects/array/flatmap": "<h1>Array.prototype.flatMap()</h1><p>Maps then flattens.</p>",
    "global_objects/array/map": """
<h1>Array.prototype.map()</h1>
<p>The <code>map()</code> method creates a new array populated with the results of calling a function.</p>
<h2 id="syntax">Syntax</h2>
<pre data-language="js">map(callbackFn)
map(callbackFn, thisArg)</pre>
<h2 id="examples">Examples</h2>
<h3 id="mapping_an_array_of_numbers">Mapping an array of numbers</h3>
<p>Squares every number.</p>
<h2 id="see_also">See also</h2>
<p><a href="../array/flatmap"><code>Array.prototype.flatMap()</code></a></p>
""",
}


class FakeDevDocs:
    """Serves the fixture data and records every request."""

    def __init__(self):
        self.catalog = copy.deepcopy(CATALOG)
        self.indexes = {
            "python~3.12": PYTHON_INDEX,
            "python~3.11": PYTHON_INDEX,
            "javascript": JAVASCRIPT_INDEX,
        }
        self.pages = {
            "python~3.12": PYTHON_PAGES,
            "python~3.11": PYTHON_PAGES,
            "javascript": JAVASCRIPT_PAGES,
        }
        self.requests: Counter[str] = Counter()
        self.offline = False

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        self.requests[f"{url.host}{url.path}"] += 1
        if self.offline:
            raise httpx2.ConnectError("network is down", request=request)
        if str(url).split("?")[0] == MANIFEST_URL:
            return httpx2.Response(200, json=self.catalog)
        if url.host == "documents.devdocs.test":
            slug, _, rest = url.path.lstrip("/").partition("/")
            if slug in self.indexes:
                if rest == "index.json":
                    return httpx2.Response(200, json=self.indexes[slug])
                if rest == "db.json":
                    return httpx2.Response(200, json=self.pages[slug])
                if rest.endswith(".html") and rest[:-5] in self.pages[slug]:
                    return httpx2.Response(200, text=self.pages[slug][rest[:-5]])
        return httpx2.Response(404, text="Not Found")

    def count(self, fragment: str) -> int:
        return sum(n for key, n in self.requests.items() if fragment in key)


@pytest.fixture
def fake() -> FakeDevDocs:
    return FakeDevDocs()


@pytest.fixture
def store(tmp_path, fake) -> DevDocsStore:
    settings = Settings(
        data_dir=tmp_path / "data",
        manifest_url=MANIFEST_URL,
        documents_url=DOCUMENTS_URL,
    )
    store = DevDocsStore(settings, http=httpx2.Client(transport=httpx2.MockTransport(fake.handler)))
    yield store
    store.close()

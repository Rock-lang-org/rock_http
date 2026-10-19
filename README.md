# rock_http

`rock_http` is a safe, blocking HTTP/1.1 server library for Rock. It provides
bounded request parsing, ordered headers, response framing, and a
thread-per-connection server over the standard library's safe networking and
threading APIs.

## Safety

The crate contains no `unsafe`, raw pointers, libc calls, compiler intrinsics,
or file-descriptor access. Low-level operations remain encapsulated by safe
standard-library types such as `TcpListener`, `TcpStream`, `Vec`, `String`,
`Arc`, and `spawn_detached`.

## Dependency

With Rock `v0.7.0`, clone this repository beside your application and add a
path dependency (paths are relative to your application's `rock.toml`):

```sh
git clone https://github.com/rock-lang-org/rock_http ../rock_http
```

```toml
[dependencies]
rock_http = { path = "../rock_http" }
```

## Server

```rock
> std::io::net::SocketAddrV4
> std::control::Result
> rock_http::request::Request
> rock_http::response::Response
> rock_http::server::Server

handle: Request -> Response
handle = request ->
    if request.target.as_str! == "/health"
        Response::text 200, "ok"
    else
        Response::text 404, "not found"

main = ->
    address = SocketAddrV4::localhost 8080
    match Server::bind address
        Result::Ok server =>
            result = server.serve handle
            0
        Result::Err _ => 1
```

`Server::serve` shares the handler safely and detaches one owned worker per
connection. A request or response failure closes only that connection.

Ordinary programs should prefer `main = !->`: it evaluates the body for effects,
discards its result, and returns unit (`()`), which maps to process exit status
`0` without a final `0`. This example intentionally keeps `main = ->` to return
status `1` if binding fails and `0` if serving returns. Changing it to the discard
arrow would discard those statuses, not propagate failure to the process.

## Protocol Scope

The initial release supports:

- HTTP/1.0 and HTTP/1.1 request lines.
- Fixed request bodies through `Content-Length`.
- Requests fragmented across arbitrary TCP reads.
- Ordered duplicate headers with case-insensitive lookup.
- Text and binary responses.
- Exact `Content-Length` response framing.
- One request and response per connection with `Connection: close`.
- Configurable request-line, header, header-count, and body limits.
- `400`, `413`, `431`, and `501` protocol error responses.

The initial release intentionally does not support chunked transfer coding,
keep-alive, pipelining, TLS, compression, multipart parsing, WebSockets,
async I/O, HTTP/2, or HTTP/3.

## Build

Install the [Rock toolchain](https://rock-lang-org.github.io/Rock/getting-started/installation.html)
and select `v0.7.0`, the version tested by this repository's CI:

```sh
rockup install v0.7.0
rockup default v0.7.0
```

Binary releases require Linux x86_64, glibc 2.39 or newer (Ubuntu 24.04),
and a C linker (`build-essential`); Rust and LLVM are not needed.

This repository root is a library, not an executable. Build its reusable
artifact from the root:

```sh
rock artifact
rock artifact --release
```

Rock 0.7.0's `rock build` attempts an executable build even when the manifest
entry is named `src/lib.rk`; it does not select a library build automatically.
Running it at this repository root produces misleading import/type errors.
Use `rock artifact` here instead.

To build and run the standalone server application:

```console
$ cd example
$ rock build
$ rock run
```

The example uses `rock_http = { path = ".." }` to build the local library
and listens on `127.0.0.1:8080`. Try `curl http://127.0.0.1:8080/health`.

## Tests

From the repository root, with Python 3 and the installed toolchain:

```sh
python3 tests/integration.py
python3 tests/integration.py --release
```

The runner uses `rockc` directly to build a library artifact and both fixtures
in a temporary directory; it never builds the Rust compiler or needs a Rock
source checkout. It discovers the installed `std.rkca` with
`rockc --print target-libdir` and passes it explicitly using `--extern-artifact`
for every compilation. It checks parser errors and limits, exact response framing,
GET, HEAD, fragmented and binary POST, UTF-8 and embedded-NUL response framing,
invalid request bytes, unsupported transfer coding, recovery after an error,
and four concurrent clients against a real localhost server. Processes
and socket reads have timeouts, and the server and temporary files are cleaned up.

Override the compiler with `ROCKC` or `--rockc`; `--sysroot` selects a matching
installed sysroot when needed:

```sh
python3 tests/integration.py --rockc /opt/rock/bin/rockc --sysroot /opt/rock
```

Use `rock build` from `example/` to check the local manifest dependency workflow.

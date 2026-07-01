# syntax=docker/dockerfile:1
#
# llama.cpp variant of the engine image (see engine.Dockerfile for the vLLM one,
# engine-sglang.Dockerfile for SGLang). Multi-backend design: each inference engine
# gets its own backend image, built FROM that engine's official base, because the
# launcher spawns the engine as a subprocess *inside this container*
# (apps/backend/app/llmops/process.py). So "which engines a backend can launch" ==
# "what's installed in its image". Here the base provides the `llama-server` binary.
# See docs/multi-backend-engine-design_zh-CN.md §5 and docs/llamacpp-launcher-impl-design_zh-CN.md.
#
# This image runs the control-plane backend (which shells out to `llama-server`).
# It is byte-for-byte the same backend code as the vLLM/SGLang images — only the
# base (= the engine CLI available) differs.
FROM ghcr.io/ggml-org/llama.cpp:server-cuda

WORKDIR /app

# The llama.cpp server-cuda base ships python3.12 but NO pip/ensurepip and no build
# toolchain (it's runtime-only). Bootstrap pip via apt, then install our deps into an
# isolated venv (avoids PEP-668 externally-managed friction on the Ubuntu base). The
# venv's bin goes on PATH so the compose `command:` (uvicorn) resolves from it, while
# `llama-server` stays reachable from the base image's own PATH entry.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# The base ships the server binary at /app/llama-server (its ENTRYPOINT) but /app is
# not on PATH, and we clear the ENTRYPOINT below so the compose `command:` (uvicorn)
# runs. Symlink it onto PATH so the launcher can spawn a bare `llama-server`
# (LlamacppLauncher command[0]) without hardcoding /app.
RUN ln -sf /app/llama-server /usr/local/bin/llama-server
# llama-server's shared libs (libllama-server-impl.so, libggml-*.so) live in /app and
# are found via an $ORIGIN rpath only when invoked as /app/llama-server directly. Once
# spawned through the PATH symlink that resolution breaks, so put /app on the library
# search path explicitly (keeping the base's CUDA libs). Verified: without this,
# `llama-server` exits rc=127 "libllama-server-impl.so: cannot open shared object file".
ENV LD_LIBRARY_PATH=/app:/usr/local/cuda/lib64

COPY apps/backend/requirements.txt /tmp/backend-req.txt
COPY apps/router-server/requirements.txt /tmp/router-req.txt
# This image only launches llama.cpp:
#  - vllm / sglang: other engines, not installed here (and vllm is multi-GB).
#  - bitsandbytes: on-the-fly quant for the *vLLM* subprocess; it requires torch,
#    which this base does NOT ship. llama.cpp uses GGUF quantization instead, so drop
#    it (keeping it would drag a multi-GB torch install).
#  - pytest*: dev-only.
RUN sed -i -E '/^(vllm|sglang|bitsandbytes.*|pytest.*)$/d' /tmp/router-req.txt /tmp/backend-req.txt \
    && pip install --no-cache-dir -r /tmp/backend-req.txt -r /tmp/router-req.txt

# App code + shared packages — same layout as the vLLM/SGLang images so the in-code
# sys.path bootstrap and default config/overlay/db paths resolve to /app.
COPY apps/backend ./apps/backend
COPY apps/router-server ./apps/router-server
COPY packages ./packages

# The llama.cpp base image sets its own ENTRYPOINT (llama-server); clear it so the
# compose `command:` (uvicorn for the backend) runs verbatim.
ENTRYPOINT []
CMD ["bash"]

# The base also ships a HEALTHCHECK probing llama-server on :8080, but this container
# runs the control-plane backend (uvicorn on :5000), not llama-server — so the
# inherited check always fails and the container shows "unhealthy". Clear it to match
# the vLLM/SGLang engine images (which define no healthcheck).
HEALTHCHECK NONE

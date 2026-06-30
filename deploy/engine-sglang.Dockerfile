# syntax=docker/dockerfile:1
#
# SGLang variant of the engine image (see engine.Dockerfile for the vLLM one).
# Multi-backend design: each inference engine gets its own backend image, built
# FROM that engine's official base, because vLLM / SGLang pin conflicting
# torch/CUDA/flashinfer and the launcher spawns the engine as a subprocess *inside
# this container* (apps/backend/app/llmops/process.py). So "which engines a backend
# can launch" == "what's installed in its image".
#
# This image runs the control-plane backend (which shells out to
# `python -m sglang.launch_server`). It is byte-for-byte the same backend code as
# the vLLM image — only the base (= the engine CLI available) differs.
# See docs/multi-backend-engine-design_zh-CN.md §5.
FROM lmsysorg/sglang:latest

WORKDIR /app

COPY apps/backend/requirements.txt /tmp/backend-req.txt
COPY apps/router-server/requirements.txt /tmp/router-req.txt
# vllm is not in this base and we don't want it (this image only launches SGLang);
# sglang is already in the base; pytest* are dev-only. Drop them so we don't pull a
# multi-GB vllm wheel or reinstall sglang.
RUN sed -i -E '/^(vllm|sglang|pytest.*)$/d' /tmp/router-req.txt /tmp/backend-req.txt \
    && pip install --no-cache-dir -r /tmp/backend-req.txt -r /tmp/router-req.txt

# App code + shared packages — same layout as the vLLM image so the in-code
# sys.path bootstrap and default config/overlay/db paths resolve to /app.
COPY apps/backend ./apps/backend
COPY apps/router-server ./apps/router-server
COPY packages ./packages

# The SGLang base image sets its own ENTRYPOINT; clear it so the compose
# `command:` (uvicorn for the backend) runs verbatim.
ENTRYPOINT []
CMD ["bash"]

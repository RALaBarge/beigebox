# BeigeBox

BeigeBox is a modular, OpenAI-compatible LLM middleware platform designed to give engineers architectural control over their AI stack.

It sits between your frontend and your model providers, handling routing, orchestration, logging, evaluation, and policy decisions while remaining provider-agnostic and locally reproducible.

It is both:

- A middleware control plane for LLM traffic
- A runnable full-stack environment (API + models + UI) deployable via Docker Compose

---

## Quick Start

BeigeBox runs as a multi-service stack using Docker Compose.

```bash
git clone https://github.com/ralabarge/beigebox.git
cd beigebox/docker
docker compose up -d

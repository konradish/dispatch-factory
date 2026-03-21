# I Built a Factory Control Plane for AI Coding Agents in One Afternoon

I've been running AI coding agents overnight for months. Claude Code workers churn through tickets while I sleep — planning, coding, reviewing, deploying. A 7-phase pipeline handles the whole SDLC autonomously.

But I had no dashboard. Just CLI commands (`dispatch --brief`, `dispatch --status`) and ntfy push notifications. I knew what shipped. I didn't know what was *happening*.

Then I read Jess Martin's post about the Software Factory pattern, and something clicked.

## The Pattern

Martin describes three layers of alternating determinism:

```
Non-deterministic: Factory Operator (monitors, unblocks, fixes)
  └─ Deterministic: Workflow Pipeline (stations, phases, gates)
       └─ Non-deterministic: LLM Workers (implementation at each station)
```

The insight that hit me: **the deterministic layer isn't scaffolding to discard as models get better. It's the control plane humans need to steer autonomous production.**

I'd independently built almost exactly this architecture. My `dispatch` pipeline has seven phases (Planner → Worker → Reviewer → Verifier → Monitor → Reporter), adaptive error healing, a watchdog cron, and visual validation. The three-layer pattern was already there.

What was missing was the *surface*. The human interaction layer. The dashboard.

## One Afternoon, Wine, and Cheese

So I built it. In one session. Here's what happened:

**First**, I had Claude Code generate a throwaway HTML mockup of what the ideal control plane would look like — an ops dashboard with assembly-line visualization, ticket flow, operator panel. This wasn't code to ship. It was thinking-out-loud in HTML.

**Then** I scaffolded the real thing:
- FastAPI backend that reads dispatch artifacts from the filesystem
- React + Vite + Tailwind frontend with pipeline view, ticket creation, terminal embedding
- Security baked in from line 1: localhost-only, read-only default, subprocess array args, no telemetry

**The "holy shit" moment** was the terminal. Each dispatch worker runs in a named tmux session. I installed [ttyd](https://github.com/tsl0922/ttyd) (one binary), wired up the API to spawn `ttyd -R` per worker, and embedded the terminal in an iframe. Click "Attach Terminal" → live tmux session streaming in the browser via WebSocket.

I dispatched a real ticket from the browser's Create Ticket form, watched it appear in the pipeline view, clicked to attach the terminal, and saw the worker execute. The whole loop worked.

## What I Learned

**The throwaway mockup was essential.** Not for code reuse — none of it shipped. But for thinking through what the control plane needed to *be*. The mockup had a factory floor visualization, operator panel, human controls. It shaped what the real UI focused on.

**Start with the data pipe.** The first thing I tested was whether the backend could read real dispatch artifacts and return sensible state. 365 sessions parsed correctly on the first try. Everything else built on that foundation.

**Security from commit 1, not commit N.** Localhost-only binding, controls disabled by default, terminal read-only. These aren't features to add later — they're constraints that shape every design decision. When you know the tool will be open source, you code differently.

**The terminal is the killer feature.** Pipeline state, ticket creation — that's expected. But watching a live AI worker session stream in your browser? That's the moment it goes from "monitoring tool" to "control plane."

## The Software Factory Question

Martin asks: will this pattern get Bitter Lessoned? Will models eventually handle the entire SDLC as a single agent, making the deterministic pipeline unnecessary?

After six months of running this overnight, my answer is: **the pipeline stays, but for humans, not models.** The LLM might not need the structure. But I need checkpoints to monitor, gates to intervene at, and a surface to steer from. That's not a limitation — it's the interface.

The factory floor isn't a constraint on the workers. It's the control plane for the operator.

## Try It

[dispatch-factory](https://github.com/konradish/dispatch-factory) is open source (Apache 2.0). It works with any pipeline that runs workers in tmux sessions and writes JSON artifacts. You don't need my specific `dispatch` tool — just named tmux sessions and artifact files.

```bash
git clone https://github.com/konradish/dispatch-factory.git
cd dispatch-factory
make install
cp .dispatch-factory.example.toml .dispatch-factory.toml
# Edit config to point at your artifacts directory
make dev
```

The factory is open. Come build on it.

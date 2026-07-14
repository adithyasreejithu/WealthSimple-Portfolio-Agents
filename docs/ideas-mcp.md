# MCP & Scheduling Architecture for WealthSimple Portfolio Agents

**Date:** July 8, 2026  
**Status:** Planning Phase  
**Scope:** Email extraction + statement processing + yfinance data scheduling with Claude integration

---

## Executive Summary

Based on analysis of MCP servers, scheduling solutions, and your specific use case (laptop-based with email + statements + yfinance extraction), we're exploring a **hybrid approach**:

1. **Local MCP servers** (email + scheduler) for Claude visibility and control
2. **System-level scheduling** (cron or Hermes Agent) for reliable task execution independent of machine state
3. **Web agents** (planned) to automate data collection

This document captures the decision rationale, technical approach, and remaining open questions.

---

## Context & Constraints

### Your Infrastructure
- **Machine:** Laptop with integrated GPU (sleeps/shuts down regularly) ❌ Not suitable for simple cron alone
- **Email Sources:** Gmail + manual export (web agents planned)
- **Data Pipeline:** Email extraction → Statement processing → yfinance enrichment
- **Claude Integration:** Optional but valuable for status/control

### Key Requirement
**Must handle scheduled tasks independent of machine uptime.** Simple system cron won't work if laptop sleeps through scheduled run times.

---

## Solutions Evaluated

### Option 1: System Cron (Rejected)
**Pros:**
- Simplest implementation (3 lines of config)
- Zero cost, zero complexity
- Direct CLI integration with `python src/app.py`

**Cons:**
- ❌ Unreliable on laptop that sleeps/shuts down
- ❌ Tasks miss scheduled run times if machine is off
- ❌ No recovery mechanism

**Status:** ⛔ Not suitable for this use case

---

### Option 2: Hermes Agent
**Pros:**
- ✅ Native cron scheduling (supports "every day at 9am", cron expressions, durations)
- ✅ Runs independently of machine state (daemon or cloud deployment)
- ✅ Claude integration via MCP bridge (free)
- ✅ Open-source and free
- ✅ Built-in task visibility and recovery

**Cons:**
- Designed for agentic/decision-making tasks, not deterministic pipelines
- Adds another tool to maintain
- Overkill if you just want dumb scheduling

**Best for:** Flexible automation where Claude needs visibility and control over task execution

**Cost:** $0 if running on laptop, $5-10/month if cloud-deployed for always-on execution

**Status:** ✅ Viable option

---

### Option 3: Cloud Scheduler (GitHub Actions / AWS EventBridge)
**Pros:**
- ✅ Reliable 24/7 execution independent of local machine
- ✅ Built-in monitoring, logging, retry logic
- ✅ Scales easily

**Cons:**
- Requires deploying pipeline to cloud (AWS Lambda, GitHub runner, etc.)
- More complex setup
- Small monthly cost (~$1-5/month)

**Best for:** Production pipelines that must run regardless of developer machine state

**Status:** ✅ Viable but overkill for MVP

---

### Option 4: Hybrid MCP + System Integration
**Recommended approach for MVP**

**Architecture:**
```
Claude Desktop (optional visibility)
    ↓
    MCP Servers (local)
    ├─ email-mcp (fetch emails, send, track)
    ├─ scheduler-mcp (manage cron tasks)
    └─ status-mcp (query pipeline state)
    ↓
    System Scheduler (background)
    ├─ Hermes Agent daemon (recommended)
    └─ OR cloud cron job (future)
    ↓
    Pipeline Execution
    └─ python src/app.py extract-emails && process-statements && enrich-yfinance
```

**Pros:**
- ✅ MCP servers give Claude real-time visibility
- ✅ Hermes handles reliable scheduling independent of laptop
- ✅ Flexible—can move Hermes to cloud later without changing MCP code
- ✅ Modular—each concern (email, scheduling, status) is a separate MCP
- ✅ Scalable—web agents can eventually replace email extraction

**Cons:**
- More moving parts than simple cron
- Requires setting up multiple MCP servers

**Status:** ✅ **RECOMMENDED**

---

## Technical Approach

### Phase 1: Local MCP Servers (No scheduling)

**Deliverables:**
- `email-mcp.py` — Tools to fetch/send/track emails (Gmail API or IMAP)
- `scheduler-mcp.py` — Tools to list, create, monitor scheduled tasks
- `status-mcp.py` — Tools to query pipeline execution status from database

**Testing:** Each MCP exposable in Claude Desktop, testable interactively

**Cost:** $0

**Timeline:** 1-2 weeks

---

### Phase 2: Scheduling Backend

**Option A: Hermes Agent (Recommended)**
- Install Hermes Agent locally or on small cloud box
- Connect Hermes MCP bridge to Claude
- Define cron tasks in Hermes CLI or via Claude
- Hermes triggers your CLI commands on schedule

**Option B: AWS Lambda + EventBridge**
- Deploy pipeline to Lambda
- EventBridge cron triggers execution
- SNS notifications to Slack on completion

**Recommendation:** Start with Hermes Agent locally. Low friction, high flexibility.

**Cost:** $0 (local) or $5-10/month (cloud)

**Timeline:** 1 week

---

### Phase 3: Web Agents Integration (Planned)

Your upcoming web agents will eventually replace manual email export. At that point:
- Web agents call MCP email server to log/store results
- Scheduler still triggers extraction pipeline
- Architecture remains unchanged

---

## MCP Server Design

### Email MCP
```python
@mcp.tool()
def fetch_emails(mailbox: str = "INBOX", limit: int = 10, since_date: str = None) -> list[dict]:
    """Fetch emails from Gmail. Returns list of email objects."""
    pass

@mcp.tool()
def mark_processed(email_id: str) -> dict:
    """Mark email as processed in archive."""
    pass

@mcp.tool()
def send_email(to: str, subject: str, body: str) -> dict:
    """Send email via SMTP."""
    pass
```

### Scheduler MCP
```python
@mcp.tool()
def list_scheduled_tasks() -> list[dict]:
    """List all scheduled extraction tasks."""
    pass

@mcp.tool()
def create_task(name: str, command: str, schedule: str) -> dict:
    """Create new scheduled task. Schedule: '0 9 * * *' or 'every day at 9am'."""
    pass

@mcp.tool()
def trigger_now(task_id: str) -> dict:
    """Manually trigger a task immediately."""
    pass
```

### Status MCP
```python
@mcp.tool()
def get_pipeline_status() -> dict:
    """Return last execution time, success/failure, rows processed."""
    pass

@mcp.tool()
def get_execution_logs(task: str, limit: int = 50) -> list[str]:
    """Fetch recent execution logs for debugging."""
    pass
```

---

## Open Questions

1. **Gmail API vs IMAP?**
   - Gmail API is more reliable but requires OAuth setup
   - IMAP is simpler but less robust
   - **Decision needed:** Which authentication method do you prefer?

2. **Hermes Agent vs cloud scheduler?**
   - Hermes: More flexible, local first, can grow
   - Cloud: More reliable for production, but overkill now
   - **Decision needed:** Do you want 24/7 guaranteed execution, or is "most of the time" okay initially?

3. **Database for task tracking?**
   - Store scheduled task metadata in SQLite locally, or Postgres in cloud?
   - Track execution history for debugging?
   - **Decision needed:** How much execution visibility do you need?

4. **Web agent integration timeline?**
   - Should we design MCP servers anticipating web agents, or iterate later?
   - **Decision needed:** When will web agents be ready?

5. **Error handling & alerts?**
   - Slack notification on extraction failure?
   - Email retry logic?
   - **Decision needed:** How should the system notify you of problems?

---

## Sources & References

### MCP Architecture & Building
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [Building MCP Servers](https://py.sdk.modelcontextprotocol.io/server/)
- [Model Context Protocol Official Docs](https://modelcontextprotocol.io/docs/develop/build-server)
- [How to Build Your Own MCP Server with Python](https://www.freecodecamp.org/news/how-to-build-your-own-mcp-server-with-python/)
- [Real Python: Python MCP](https://realpython.com/python-mcp/)

### Email MCP Examples
- [Email MCP GitHub Example](https://github.com/ptbsare/email-mcp-server) — POP3/SMTP implementation
- [Building a Simple MCP Server in Python](https://machinelearningmastery.com/building-a-simple-mcp-server-in-python/)

### Scheduling Solutions
- [Cowork Scheduled Tasks](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork) — ⚠️ Has memory leaks on Windows
- [Schedule recurring tasks in Claude Code Desktop](https://code.claude.com/docs/en/desktop-scheduled-tasks)

### Hermes Agent
- [Hermes Agent Official Docs](https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent)
- [Hermes MCP Bridge GitHub](https://github.com/mlennie/hermes-mcp) — Connects Hermes to Claude
- [Claude Hermes Agent Setup Guide](https://aisuccesslabjuliangoldie.com/blog/claude-hermes-agent/)
- [Hermes vs Claude Code Comparison](https://www.mindstudio.ai/blog/hermes-agent-vs-claude-code-comparison)

### Cloud Scheduling Alternatives
- [AWS Free Tier 2026](https://aws.amazon.com/free/)
- [AWS Free Tier Documentation](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html)
- [Heroku Pricing](https://www.heroku.com/pricing/) — No free tier; $5/month minimum
- [Railway, Render, Fly.io Free Tiers](https://northflank.com/blog/heroku-vs-aws) — $5-10/month alternatives

### Related Architecture
- [Claude Code Instructions](C:\Projects\Python\WealthSimple-Portfolio-Agents\CLAUDE.md) — Your project structure & agent/skill organization
- [MCP Server Architecture Explained (May 2026)](https://www.skyvern.com/blog/mcp-server-architecture-explained/)

---

## Next Steps

1. **Answer open questions above** — This will clarify scope and dependencies
2. **Design MCP server structure** — Define tools, resources, and transport (stdio vs HTTP)
3. **Prototype email-mcp** — Validate Gmail API integration or IMAP setup
4. **Integrate with existing pipeline** — Wire MCP tools into `app.py` CLI structure
5. **Set up Hermes Agent** — Test cron scheduling with your extraction commands
6. **Test end-to-end** — Laptop sleeps, tasks still run, Claude can query status

---

## Estimated Timeline (if moving forward)

- **Week 1:** Email MCP + Scheduler MCP setup
- **Week 2:** Hermes Agent integration + testing
- **Week 3:** Status MCP + execution logging
- **Week 4:** End-to-end testing, documentation, deploy to production

**Total effort:** 3-4 weeks for MVP

---

**Created:** July 8, 2026  
**Author:** Claude + Adithya  
**Status:** Ready for review and decision on open questions

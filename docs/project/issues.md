# WealthSimple Portfolio AI Agent - Issues, Risks & Security

## Overview

This document outlines the issues, challenges, risks, and security implications of building an AI agent to download stock trades and portfolio data from WealthSimple.

---

## Issues That May Arise

### 1. **2FA/OTP Requirement** ⚠️
**Likelihood**: Certain | **Impact**: Medium | **Severity**: High

WealthSimple requires 2FA for account access (TOTP-based codes).

**Issues**:
- Cannot fully automate login without manual OTP entry
- TOTP codes expire every 30 seconds (timing window is tight)
- User must have authenticator app set up beforehand

**Solutions**:
- Support TOTP input via interactive CLI prompt
- Use `pyotp` library for TOTP validation
- Document 2FA setup process clearly
- Store TOTP seed securely in OS keyring (encrypted)

---

### 2. **API GraphQL Queries Fail / Structure Changes** ⚠️
**Likelihood**: Medium | **Impact**: High | **Severity**: Medium

The GraphQL API is not officially documented. WealthSimple doesn't publish it, so query structures may change without notice.

**Issues**:
- Query fields may be renamed or removed
- Response structure could change after API updates
- No official changelog or deprecation notices
- Parsing fails silently if fields are missing

**Solutions**:
- Implement graceful error handling for missing fields
- Add API version detection in response headers
- Log API changes and notify user
- Validate response schema before parsing
- Implement retry logic with exponential backoff
- Monitor existing library implementations (`ws-api-python`, `wealthsimple-python`) for API changes

---

### 3. **Token Expiry & Mid-Operation Failures** ⚠️
**Likelihood**: Certain | **Impact**: High | **Severity**: High

OAuth tokens expire (~30 minutes). A sync that takes longer than token lifetime will fail mid-operation.

**Issues**:
- Sync starts but fails halfway through
- Partial data stored in database (inconsistency)
- User doesn't know if sync succeeded or failed
- Token refresh may fail if network issues occur

**Solutions**:
- Auto-refresh tokens 5 minutes before expiry
- Check token validity before each API call
- Implement transaction-based storage (rollback on failure)
- Log all failures with timestamps
- Provide clear user feedback on sync success/failure
- Store refresh tokens securely for token renewal

---

### 4. **Duplicate Trades & Data Inconsistency** ⚠️
**Likelihood**: Medium | **Impact**: Medium | **Severity**: High

Running sync multiple times could create duplicate records.

**Issues**:
- Same trade appears multiple times in database
- Holdings updated multiple times (creating stale data)
- Portfolio summary shows inflated totals
- CSV exports contain duplicates

**Solutions**:
- Use upsert logic (insert or update, never duplicate)
- Define composite keys: `(account_id, date, symbol, action, quantity)` for trades
- Define composite keys: `(account_id, symbol)` for holdings
- Use database transactions to ensure atomicity
- Test sync logic with multiple runs
- Implement idempotent operations

---

### 5. **Credential Storage Issues** ⚠️
**Likelihood**: Low | **Impact**: Critical | **Severity**: Critical

OS keyring may not be available or may fail to store/retrieve credentials.

**Issues**:
- Keyring unavailable on headless servers (no GUI)
- Different OS (Windows/Mac/Linux) have different keyring implementations
- Keyring could be locked or require OS-level permissions
- Fallback mechanism (plaintext files) is insecure

**Solutions**:
- Use `keyring` library with platform detection
- Test keyring on target OS before production use
- Provide clear error messages if keyring fails
- Optional fallback to environment variables (development only)
- Document OS keyring setup requirements
- Never fall back to plaintext file storage

---

### 6. **Network & API Timeouts** ⚠️
**Likelihood**: Low-Medium | **Impact**: Medium | **Severity**: Medium

Network issues, API slowness, or temporary outages cause requests to timeout.

**Issues**:
- Sync fails entirely if any API call times out
- User doesn't know if data was partially fetched
- No automatic recovery
- Silent failures without proper logging

**Solutions**:
- Implement exponential backoff retry logic (3 attempts max)
- Set reasonable timeouts (30-60 seconds per request)
- Log all network errors with timestamps
- Notify user of retries and final status
- Implement circuit breaker pattern for repeated failures
- Provide manual retry option

---

### 7. **WealthSimple Rate Limiting** ⚠️
**Likelihood**: Unknown | **Impact**: High | **Severity**: Medium

No public documentation on WealthSimple API rate limits.

**Issues**:
- Unknown rate limits could be triggered
- API may return 429 (Too Many Requests) or 403 (Forbidden)
- Repeated violations could trigger IP blocking
- No way to know if we've hit the limit

**Solutions**:
- Implement conservative delays between API calls (1-2 seconds)
- Monitor response headers for rate limit information
- Implement graceful backoff if 429 received
- Log all rate limit errors
- Avoid concurrent requests to same API
- Document conservative usage patterns

---

### 8. **Account Detection & Blocking** ⚠️
**Likelihood**: Low-Medium | **Impact**: High | **Severity**: Medium

WealthSimple may detect automated access and block the account.

**Issues**:
- Basic bot detection (user agents, request patterns)
- Repeated API calls from same IP could trigger blocks
- Account could be locked without warning
- IP address could be rate-limited or banned

**Solutions**:
- Use realistic user agents and browser headers
- Add delays between requests (mimic human behavior)
- Only run during reasonable hours (optional)
- Monitor for 403/401 errors (access denied)
- Log all unusual API responses
- Consider rotating user agents if needed

---

## Critical: Legal & TOS Risk

### ⚠️ **WealthSimple Terms of Service Violation**

**WealthSimple TOS Section 13** explicitly states:
> "The framing, mirroring, **scraping or data-mining** of the Site or any of its content **in any form and by any method is strictly prohibited**."

### Your Risks:

| Risk | Probability | Impact |
|------|-------------|--------|
| **Account Termination** | Medium | Critical — Account closed, trading disabled |
| **IP Blocking** | Low | High — Cannot access WealthSimple |
| **Legal Action** | Low | Critical — Potential computer fraud liability |
| **Data Access Revocation** | Medium | High — API access removed, agent breaks |

### Mitigations:

✅ **Use GraphQL API** (not web scraping) — More defensible legally than Selenium/Playwright  
✅ **Access only your own account** — Not third-party or aggregated data  
✅ **Document your use case** — Personal finance tracking, non-commercial  
✅ **Display legal warning** — User must explicitly consent on first run  
✅ **Consider requesting permission** — Email WealthSimple support asking for blessing  
✅ **Implement access controls** — Log all data accesses, audit regularly  

---

## Security Implications & Risks

### 🔴 **Critical: Credential Management Risk**

Your credentials unlock your entire trading account. Compromise = account takeover, unauthorized trades, fund theft.

**Risks**:
- Plaintext password storage → Account compromise
- Credentials in logs/version control → Public exposure
- Token leakage → Attackers can trade on your behalf
- Credential injection attacks → Malicious code steals passwords

**Mitigations**:
- ✅ Store passwords in **OS keyring** (encrypted by OS), not files
- ✅ Never store passwords on disk; use OAuth tokens instead
- ✅ OAuth tokens have short expiry (~30 min) — lower impact if leaked
- ✅ Use `getpass` module for interactive input (no echo to console)
- ✅ Never log credentials; audit all credential access
- ✅ Clear credentials from memory immediately after use
- ✅ Use environment variables only in CI/testing (never production)
- ✅ Implement credential rotation mechanism

**Best Practice**:
```python
# ✅ Good: Credentials in OS keyring
import keyring
keyring.set_password("app", "email", "user@example.com")

# ❌ Bad: Credentials in plaintext file
with open("creds.json", "w") as f:
    json.dump({"password": "secret123"}, f)
```

---

### 🟠 **High: API Reverse-Engineering Risk**

The GraphQL API is not officially documented. WealthSimple doesn't publish it, so changes are unpredictable.

**Risks**:
- API structure could change without notice
- GraphQL queries may be blocked if detected as automated
- New anti-scraping protections could break agent
- WealthSimple could revoke API access via IP blocking or account suspension

**Mitigations**:
- ✅ Graceful error handling (notify user, don't crash silently)
- ✅ API version detection in response headers
- ✅ Comprehensive logging of all API responses
- ✅ Retry logic with exponential backoff for transient failures
- ✅ Monitor existing libraries (`ws-api-python`, `wealthsimple-python`) for API changes
- ✅ Reference battle-tested GraphQL queries from open-source projects
- ✅ Document API structure assumptions in code comments

---

### 🟠 **High: 2FA & TOTP Seed Risk**

TOTP seed (the secret used to generate codes) is extremely sensitive. If leaked, attacker can generate valid codes.

**Risks**:
- TOTP seed stored insecurely → Attacker bypasses 2FA
- TOTP codes printed in logs → Attacker sees valid codes
- Seed exported with credentials → Complete account compromise
- Memory exposure → Malware could extract seed from RAM

**Mitigations**:
- ✅ Store TOTP seed in **OS keyring only** (encrypted by OS)
- ✅ Never print/log TOTP codes or seeds
- ✅ Use `pyotp` library (battle-tested TOTP implementation)
- ✅ Clear TOTP codes from memory after use
- ✅ Implement TOTP setup verification (test code generation)
- ✅ Document: Never share/export your TOTP seed
- ✅ Allow user to disable TOTP seed storage (manual entry only)

---

### 🟡 **Medium: Local Database Encryption Risk**

Your trade history stored locally in unencrypted SQLite file.

**Risks**:
- SQLite database on unencrypted disk → Physical theft exposes data
- Backups unencrypted → Copies contain sensitive data
- Malware on device → Can read .db file directly
- No access control → Any user on machine can read database

**Mitigations**:
- ✅ Use OS-level encryption: Windows BitLocker, macOS FileVault, Linux LUKS
- ✅ Document database location clearly
- ✅ Implement regular backups with encryption
- ✅ Restrict file permissions: `chmod 600 portfolio.db` (Unix-like)
- ✅ Optionally implement SQLite encryption (library: `sqlalchemy-utils` with `pycipher`)
- ✅ Never store database in cloud (OneDrive, Google Drive, etc.)

**Note**: OS-level encryption is usually sufficient for single-user machines.

---

### 🟡 **Medium: Data Privacy Risk**

Your trade history, holdings, and account data are sensitive.

**Risks**:
- Exported CSV files contain unencrypted data
- Email backups expose data in cloud
- Data shared with others (spouse, accountant) without encryption
- Account data could be used for social engineering

**Mitigations**:
- ✅ Always encrypt exported files before sharing
- ✅ Use password-protected archives (7-Zip, WinRAR)
- ✅ Document: Never email or cloud-share unencrypted data
- ✅ Implement data minimization: Only download what you need
- ✅ Regular purging of old data (retention policy)
- ✅ Clear audit logs periodically

---

### 🟢 **Low: Audit Logging Risk**

Agent logs all credential access and data fetches (without storing credentials).

**Risks**:
- Audit logs could contain sensitive information (email hashes, etc.)
- Log files accessible by other users on machine
- Logs accumulate over time (disk space)

**Mitigations**:
- ✅ Never log plaintext credentials, passwords, or tokens
- ✅ Hash email addresses in audit logs (SHA256)
- ✅ Rotate and purge old logs (retention: 90 days)
- ✅ Restrict log file permissions (`chmod 600 agent.log`)
- ✅ Log only essential info: timestamps, event types, success/failure
- ✅ Document: Review logs regularly for suspicious activity

---

## Summary of Key Security Practices

| Practice | Priority | Implementation |
|----------|----------|-----------------|
| Use OS Keyring for credentials | Critical | `keyring` library |
| Never store passwords on disk | Critical | Only OAuth tokens + refresh tokens |
| Hash email in logs | Critical | SHA256 hashing |
| Implement token auto-refresh | High | Check expiry 5 min before refresh |
| Graceful error handling | High | Try-catch all API calls, log errors |
| Use `getpass` for password input | High | No echo to console |
| Validate TOTP codes | High | `pyotp.TOTP.verify(code)` |
| Encrypt exported data | Medium | Recommend user encryption |
| Use OS-level encryption | Medium | BitLocker/FileVault/LUKS |
| Rotate credentials regularly | Medium | `--rotate-credentials` CLI flag |

---

## Testing & Verification Checklist

- [ ] Credentials never appear in logs or stderr
- [ ] TOTP setup works without leaking seed
- [ ] Token auto-refresh triggers before expiry
- [ ] Failed TOTP attempts don't leak account state
- [ ] Network errors trigger retry logic (max 3 attempts)
- [ ] Sync runs twice, no duplicate trades created
- [ ] Database file has restricted permissions (0600)
- [ ] Exported CSV files are created without world-readable permissions
- [ ] All API errors logged with timestamps
- [ ] User sees legal disclaimer on first run
- [ ] User confirms understanding of risks before first sync

---

## Recommendations

### Before Going Live:

1. **Review Legal Risks** — Have legal counsel review TOS implications (optional but recommended)
2. **Contact WealthSimple** — Request permission for API usage
3. **Test with Small Amounts** — Verify agent works before relying on it
4. **Document Use Case** — Personal finance tracking, non-commercial, own account only
5. **Set Up 2FA** — Ensure authenticator app is configured beforehand
6. **Enable OS Encryption** — BitLocker, FileVault, or LUKS for data protection
7. **Review All Logs** — Before first production run, audit what gets logged

### Ongoing Operations:

- Monitor API response errors regularly
- Review audit logs for suspicious activity
- Rotate credentials every 30-90 days
- Keep Python dependencies updated (security patches)
- Test recovery procedures (what if sync fails?)
- Maintain database backups (encrypted)
- Monitor WealthSimple announcements for API changes

---

## Conclusion

Building this agent is technically feasible, but comes with **significant legal and security risks**. The main issues are:

1. **TOS Violation** — WealthSimple explicitly forbids scraping (mitigation: use API, not scraping)
2. **API Instability** — Reverse-engineered API can change without notice (mitigation: robust error handling)
3. **Credential Security** — Passwords/tokens are high-value targets (mitigation: OS keyring + token auto-refresh)
4. **2FA Requirement** — Manual OTP entry needed (mitigation: interactive CLI input)
5. **Data Consistency** — Duplicates possible on retries (mitigation: upsert logic)

**Recommendation**: Use this agent only if you:
- Understand and accept the legal risks
- Have requested WealthSimple's permission (optional)
- Use secure credential storage (OS keyring)
- Maintain regular backups
- Monitor logs for suspicious activity
- Consider this a personal tool, not for production/commercial use

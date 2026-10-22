/** Independently authored public Paperclip adapter; no Paperclip client or task writes. */
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { spawn } from "node:child_process";

const codes = { succeeded: 0, failed: 1, cancelled: 3, attention_required: 4, refused: 5, uncertain: 6 };
const id = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;
const sensitive = /PAPERCLIP|TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|API_KEY/i;

function fail(code) { throw new Error(code); }
function object(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid_object");
  return value;
}
function identifier(value) {
  if (typeof value !== "string" || !id.test(value)) fail("invalid_identity");
  return value;
}

// JSON.parse alone accepts duplicate keys. Detect them recursively before using configuration.
function strictJson(raw, limit) {
  if (Buffer.byteLength(raw) > limit) fail("record_too_large");
  let offset = 0;
  function whitespace() { while (/\s/.test(raw[offset] ?? "") && offset < raw.length) offset++; }
  function string() {
    const start = offset++;
    while (offset < raw.length) {
      if (raw[offset++] === '"') return JSON.parse(raw.slice(start, offset));
      if (raw[offset - 1] === "\\") offset++;
    }
    fail("invalid_json");
  }
  function value(depth = 0) {
    if (depth > 64) fail("invalid_json");
    whitespace();
    const char = raw[offset];
    if (char === '"') return string();
    if (char === "{" || char === "[") {
      const end = char === "{" ? "}" : "]";
      const keys = new Set();
      offset++; whitespace();
      if (raw[offset] === end) { offset++; return; }
      while (offset < raw.length) {
        if (char === "{") {
          whitespace(); if (raw[offset] !== '"') fail("invalid_json");
          const key = string();
          if (keys.has(key)) fail("duplicate_key");
          keys.add(key); whitespace();
          if (raw[offset++] !== ":") fail("invalid_json");
        }
        value(depth + 1); whitespace();
        if (raw[offset] === end) { offset++; return; }
        if (raw[offset++] !== ",") fail("invalid_json");
      }
      fail("invalid_json");
    }
    const match = raw.slice(offset).match(/^(?:null|true|false|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/);
    if (!match) fail("invalid_json");
    offset += match[0].length;
    if (/^-?\d/.test(match[0]) && !Number.isFinite(Number(match[0]))) fail("invalid_json");
  }
  value(); whitespace();
  if (offset !== raw.length) fail("invalid_json");
  return JSON.parse(raw);
}

function projection(ctx) {
  const agent = object(ctx.agent), context = object(ctx.context), issue = object(context.paperclipIssue);
  const issueId = identifier(issue.id);
  for (const key of ["issueId", "taskId"]) {
    if (context[key] !== undefined && context[key] !== issueId) fail("conflicting_issue");
  }
  if (typeof issue.description !== "string" || !issue.description.trim()) fail("missing_description");
  const result = { format: "millrace-paperclip-context/v1", company_id: identifier(agent.companyId),
    agent_id: identifier(agent.id), issue_id: issueId, attempt_id: identifier(ctx.runId), description: issue.description };
  if (Buffer.byteLength(JSON.stringify(result)) > 65536) fail("assignment_too_large");
  return result;
}

async function configuration(ctx) {
  const options = object(ctx.config);
  if (Object.keys(options).some(key => !["bridgeConfigFile", "timeoutSeconds"].includes(key))) fail("invalid_adapter_config");
  const file = options.bridgeConfigFile;
  if (typeof file !== "string" || file.length > 4096 || !path.isAbsolute(file)) fail("invalid_config_path");
  const config = object(strictJson(await fs.readFile(file, "utf8"), 65536));
  if (config.format !== "millrace-paperclip-bridge-config/v1") fail("invalid_config");
  const allowed = ["format", "paperclip_origin", "os_origin", "os_home", "os_credential_file", "definition", "destination_root", "binding_state_root", "helper_executable", "instance_settings", "assignment_revisions", "wait_seconds", "cleanup_seconds"];
  if (Object.keys(config).some(key => !allowed.includes(key)) || allowed.slice(0, 10).some(key => config[key] === undefined)) fail("invalid_config");
  if (typeof config.helper_executable !== "string" || !path.isAbsolute(config.helper_executable) || config.helper_executable.length > 4096) fail("invalid_helper");
  const info = await fs.stat(config.helper_executable);
  if (!info.isFile() || !(info.mode & 0o111)) fail("invalid_helper");
  const wait = config.wait_seconds ?? 300, cleanup = config.cleanup_seconds ?? 15;
  if (!Number.isInteger(wait) || wait < 1 || wait > 86400 || !Number.isInteger(cleanup) || cleanup < 5 || cleanup > 60) fail("invalid_budget");
  const timeout = options.timeoutSeconds ?? wait + cleanup + 10;
  if (!Number.isInteger(timeout) || timeout < wait + cleanup + 10 || timeout > 86470) fail("invalid_adapter_timeout");
  const settings = object(config.instance_settings);
  if (!Array.isArray(settings.path) || !Array.isArray(settings.env_allowlist)) fail("invalid_settings");
  const env = {};
  for (const name of ["HOME", "TMPDIR", ...settings.env_allowlist]) {
    if (typeof name !== "string" || !/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(name)) fail("invalid_environment");
    if (!sensitive.test(name) && process.env[name] !== undefined) env[name] = process.env[name];
  }
  env.PATH = settings.path.join(path.delimiter);
  env.PYTHONDONTWRITEBYTECODE = "1";
  return { file, config, timeout, env };
}

function result(status, diagnostic, assignment = null) {
  return { exitCode: codes[status], signal: null, timedOut: false,
    resultJson: { format: "millrace-paperclip-result/v1", status, diagnostic, attempt_id: assignment?.attempt_id ?? null },
    summary: diagnostic, errorCode: status === "succeeded" ? null : diagnostic };
}

async function execute(ctx) {
  let child = null, cancelled = Boolean(ctx.signal?.aborted), timedOut = false, directory = null;
  const abort = () => { cancelled = true; if (child && child.exitCode === null) child.kill("SIGTERM"); };
  ctx.signal?.addEventListener("abort", abort, { once: true });
  try {
    await ctx.onCancellationReady?.();
    const assignment = projection(ctx);
    if (cancelled || ctx.signal?.aborted) return result("cancelled", "cancelled_before_create", assignment);
    const selected = await configuration(ctx);
    if (cancelled || ctx.signal?.aborted) return result("cancelled", "cancelled_before_create", assignment);
    directory = await fs.mkdtemp(path.join(os.tmpdir(), "millrace-paperclip-"));
    await fs.chmod(directory, 0o700);
    const assignmentFile = path.join(directory, "assignment.json");
    await fs.writeFile(assignmentFile, JSON.stringify(assignment), { mode: 0o600, flag: "wx" });
    if (cancelled || ctx.signal?.aborted) return result("cancelled", "cancelled_before_create", assignment);
    let output = Buffer.alloc(0), overflow = false, spawnFailed = false;
    child = spawn(selected.config.helper_executable, ["paperclip", "run", "--config", selected.file, "--assignment", assignmentFile],
      { env: selected.env, stdio: ["ignore", "pipe", "pipe"], shell: false });
    child.stdout.on("data", chunk => {
      if (output.length + chunk.length > 262144) { overflow = true; child.kill("SIGTERM"); }
      else output = Buffer.concat([output, chunk]);
    });
    // Never forward raw helper errors or credentials to Paperclip logs.
    let stderrBytes = 0;
    child.stderr.on("data", chunk => { stderrBytes += chunk.length; if (stderrBytes > 262144) { overflow = true; child.kill("SIGTERM"); } });
    const timer = setTimeout(() => { timedOut = true; child.kill("SIGTERM"); }, selected.timeout * 1000);
    const hardStop = setTimeout(() => { timedOut = true; child.kill("SIGKILL"); }, (selected.timeout + 65) * 1000);
    const [exitCode, signal] = await new Promise(resolve => {
      child.once("error", () => { spawnFailed = true; resolve([null, null]); });
      child.once("close", (code, sig) => resolve([code, sig]));
    });
    clearTimeout(timer); clearTimeout(hardStop);
    if (spawnFailed || overflow || signal || timedOut) {
      const answer = result("uncertain", overflow ? "helper_output_limit" : spawnFailed ? "helper_spawn_failed" : "helper_closure_unverified", assignment);
      answer.timedOut = timedOut;
      return answer;
    }
    let report;
    try { report = object(strictJson(output.toString("utf8"), 262144)); }
    catch { return result("uncertain", "invalid_helper_result", assignment); }
    const identity = report.assignment;
    const expectedRevision = selected.config.assignment_revisions?.[assignment.issue_id] ?? 1;
    if (report.format !== "millrace-paperclip-result/v1" || !Object.hasOwn(codes, report.status)
      || report.attempt_id !== assignment.attempt_id || exitCode !== codes[report.status]
      || typeof report.summary !== "string" || report.summary.length > 4096
      || !Array.isArray(report.refs) || report.refs.length > 32
      || !identity || identity.company_id !== assignment.company_id
      || identity.issue_id !== assignment.issue_id || identity.paperclip_origin !== selected.config.paperclip_origin
      || identity.assignment_revision !== expectedRevision || report.agent_id !== assignment.agent_id
      || !/^[0-9a-f]{64}$/.test(report.assignment_sha256 ?? "")
      || !/^[0-9a-f]{64}$/.test(report.binding_digest ?? "")
      || report.os_home !== selected.config.os_home
      || typeof report.result_root !== "string" || !path.isAbsolute(report.result_root)
      || report.refs.some(ref => typeof ref !== "string" || ref.length > 4096 || path.isAbsolute(ref) || ref.includes("\\") || ref.split("/").some(part => !part || part === "." || part === ".."))
      || (report.status === "succeeded" && (!id.test(report.instance_id ?? "") || !id.test(report.run_id ?? "") || !/^[0-9a-f]{64}$/.test(report.graph_revision ?? "")))) return result("uncertain", "invalid_helper_result", assignment);
    return { exitCode, signal: null, timedOut: false, resultJson: report, summary: report.summary,
      errorCode: report.status === "succeeded" ? null : report.diagnostic ?? report.status };
  } catch {
    return result("refused", "invalid_bridge_context_or_config");
  } finally {
    ctx.signal?.removeEventListener("abort", abort);
    if (directory) await fs.rm(directory, { recursive: true, force: true });
  }
}

async function testEnvironment(ctx) {
  let checks, status;
  try {
    const selected = await configuration(ctx);
    checks = [{ code: "minimal_helper_environment", level: "info", message: "Declared helper environment names",
      detail: JSON.stringify(Object.keys(selected.env).sort()) }];
    status = "pass";
  } catch {
    checks = [{ code: "invalid_bridge_configuration", level: "error", message: "Bridge configuration or helper is unavailable" }];
    status = "fail";
  }
  return { adapterType: "millrace_graph", status, checks, testedAt: new Date().toISOString() };
}

export function createServerAdapter() {
  return { type: "millrace_graph", execute, testEnvironment };
}

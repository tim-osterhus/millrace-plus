// Test host fixtures surround the exact retained pinned Paperclip loader bytes.
import fs from "node:fs/promises";
import { registerHooks } from "node:module";
import { pathToFileURL } from "node:url";

const [plugin, loader, input] = process.argv.slice(2);
const fixture = JSON.parse(await fs.readFile(input, "utf8"));
const modules = {
  "@paperclipai/adapter-utils": "export function validateAdapterLoginCapability(m) { if ('login' in m) throw Error('host fixture expects no login capability'); }",
  "../middleware/logger.js": "export const logger = { info(){}, warn(){}, error(){} };",
  "../services/adapter-plugin-store.js": "export function listAdapterPlugins(){ return []; } export function getAdapterPluginsDir(){ throw Error('unexpected store lookup'); } export function getAdapterPluginByType(){ return null; }",
};
registerHooks({ resolve(specifier, context, next) {
  if (specifier in modules) return { url: "data:text/javascript," + encodeURIComponent(modules[specifier]), shortCircuit: true };
  return next(specifier, context);
} });
const { loadExternalAdapterPackage } = await import(pathToFileURL(loader).href);
const adapter = await loadExternalAdapterPackage("@millrace/paperclip-adapter", plugin);
const controller = new AbortController();
const ctx = { ...fixture.context, signal: controller.signal, onLog: async () => { throw Error("unexpected raw log"); },
  onCancellationReady: async () => { if (fixture.abortReady) controller.abort(); } };
if (fixture.abortBefore) controller.abort();
let timer;
if (fixture.abortAfter) timer = setTimeout(() => controller.abort(), fixture.abortAfter);
const result = fixture.diagnostics ? await adapter.testEnvironment(ctx) : await adapter.execute(ctx);
clearTimeout(timer);
console.log(JSON.stringify({ type: adapter.type, result }));

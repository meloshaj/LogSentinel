import type { Plugin } from "vite";

export function resolveFrontendReleaseSha(
  env: Record<string, string | undefined> = (
    globalThis as typeof globalThis & {
      process?: { env?: Record<string, string | undefined> };
    }
  ).process?.env ?? {},
): string {
  const configuredSha =
    env.CF_PAGES_COMMIT_SHA?.trim() ||
    env.RELEASE_SHA?.trim() ||
    env.GITHUB_SHA?.trim();
  if (configuredSha) return configuredSha;

  return "unknown";
}

export function createReleaseMarkerPlugin(
  releaseSha = resolveFrontendReleaseSha(),
): Plugin {
  return {
    name: "logsentinel-release-marker",
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "version.json",
        source: `${JSON.stringify({ release_sha: releaseSha })}\n`,
      });
    },
  };
}

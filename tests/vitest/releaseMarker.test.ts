import { describe, expect, it } from "vitest";
import {
  createReleaseMarkerPlugin,
  resolveFrontendReleaseSha,
} from "../../scripts/viteReleaseMarker";

describe("frontend release marker", () => {
  it("uses the Cloudflare Pages commit SHA before generic release values", () => {
    expect(
      resolveFrontendReleaseSha({
        CF_PAGES_COMMIT_SHA: "pages-release-sha",
        RELEASE_SHA: "generic-release-sha",
        GITHUB_SHA: "github-sha",
      }),
    ).toBe("pages-release-sha");
  });

  it("emits a JSON asset named version.json for the frontend release", () => {
    const emittedAssets: unknown[] = [];
    const plugin = createReleaseMarkerPlugin("frontend-commit-sha");
    const hook = plugin.generateBundle;

    if (typeof hook !== "function") {
      throw new Error("Release marker plugin did not define generateBundle");
    }

    hook.call(
      { emitFile: (asset: unknown) => emittedAssets.push(asset) } as never,
      {} as never,
      {} as never,
      false,
    );

    expect(emittedAssets).toEqual([
      {
        type: "asset",
        fileName: "version.json",
        source: '{"release_sha":"frontend-commit-sha"}\n',
      },
    ]);
    expect(
      JSON.parse(
        (emittedAssets[0] as { source: string }).source,
      ).release_sha,
    ).toBe("frontend-commit-sha");
  });
});

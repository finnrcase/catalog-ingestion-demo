import { IntakeWorkspace } from "@/components/intake-workspace";

export default function Page() {
  const commit = (
    process.env.NEXT_PUBLIC_APP_COMMIT_SHA ||
    process.env.VERCEL_GIT_COMMIT_SHA ||
    process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ||
    "local"
  ).slice(0, 12);
  const version = process.env.npm_package_version || "0.1.0";
  const deploymentUrl = process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "";
  const repo = process.env.VERCEL_GIT_REPO_SLUG || process.env.NEXT_PUBLIC_APP_REPO || "catalog-ingestion-demo";
  const branch = process.env.VERCEL_GIT_COMMIT_REF || process.env.NEXT_PUBLIC_APP_BRANCH || "local";
  const environment = process.env.VERCEL_ENV || process.env.NODE_ENV || "local";

  return (
    <IntakeWorkspace
      buildInfo={{
        commit,
        builtAt: new Date().toISOString(),
        version,
        repo,
        branch,
        environment,
        project: "frontend",
        rootDirectory: "frontend",
        homepageRoute: "frontend/app/page.tsx",
        settingsRoute: "frontend/components/intake-workspace.tsx",
        workflowComponent: "frontend/components/intake-workspace.tsx",
        deploymentUrl,
      }}
    />
  );
}

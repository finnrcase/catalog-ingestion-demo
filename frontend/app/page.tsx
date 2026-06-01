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

  return (
    <IntakeWorkspace
      buildInfo={{
        commit,
        builtAt: new Date().toISOString(),
        version,
        deploymentUrl,
      }}
    />
  );
}

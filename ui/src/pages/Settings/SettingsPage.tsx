import { Placeholder } from "../Placeholder";

// Owner: Settings. Tabs: Workspace | Appearance | Environment | About. DESIGN.md 4.8;
// API: GET/PUT /api/settings, GET /api/settings/env, GET /api/about (src/api/system.ts).
export default function SettingsPage() {
  return <Placeholder area="settings" title="Settings" description="Workspace, appearance, environment variables and versions." />;
}

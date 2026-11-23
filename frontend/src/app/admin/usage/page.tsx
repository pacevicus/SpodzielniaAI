import { AdminUsage } from "@/components/usage/UsagePanels";
import { RequireRole } from "@/components/auth/RequireRole";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("adminUsage");

export default function AdminUsagePage() {
  return (
    <RequireRole role="ADMIN">
      <AdminUsage />
    </RequireRole>
  );
}

import { MemberUsage } from "@/components/usage/UsagePanels";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("usage");

export default function UsagePage() {
  return <MemberUsage />;
}

import { ApiKeysSection } from "@/components/settings/ApiKeysSection";
import { ChangePasswordForm } from "@/components/settings/ChangePasswordForm";
import { useSession } from "@/hooks/useSession";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export default function Settings() {
  useDocumentTitle("Settings");
  const session = useSession();
  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight">Settings</h1>
        {session.data && <p className="mt-1 text-muted-foreground">Signed in as {session.data.email}</p>}
      </div>
      <ApiKeysSection />
      <ChangePasswordForm />
    </div>
  );
}

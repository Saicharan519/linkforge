import { useState } from "react";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useForm } from "react-hook-form";
import type { ApiKey } from "@/api/types";
import { FormMessage } from "@/components/auth/AuthLayout";
import { CopyButton } from "@/components/links/CopyButton";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { TextField } from "@/components/ui/text-field";
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from "@/hooks/useApiKeys";
import { applyApiErrors, describeError } from "@/lib/forms";
import { formatDateTime } from "@/lib/format";

type Dialogs = { kind: "create" } | { kind: "revoke"; key: ApiKey } | null;

export function ApiKeysSection() {
  const keys = useApiKeys();
  const create = useCreateApiKey();
  const revoke = useRevokeApiKey();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const [revokeError, setRevokeError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  // The plaintext key exists only in the create mutation's result. Closing the dialog resets it.
  const created = create.data;
  const closeCreated = () => {
    create.reset();
    setDialog(null);
  };

  return (
    <section aria-labelledby="keys-title" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="keys-title" className="text-2xl font-extrabold tracking-tight">
            API keys
          </h2>
          <p className="text-muted-foreground">Let a script create and read your links. Send the key in the X-API-Key header.</p>
        </div>
        <Button onClick={() => setDialog({ kind: "create" })}>
          <Plus aria-hidden="true" /> New key
        </Button>
      </div>

      <p role="status" aria-live="polite" className={notice ? "font-bold text-go" : "sr-only"}>
        {notice}
      </p>

      {keys.isPending ? (
        <LoadingRows label="Loading API keys" rows={2} />
      ) : keys.isError ? (
        <ErrorState message={describeError(keys.error)} onRetry={() => void keys.refetch()} />
      ) : keys.data.length === 0 ? (
        <EmptyState
          title="No API keys"
          body="You do not need one to use LinkForge in the browser. Create a key to call the API from a script."
        />
      ) : (
        <ul aria-label="Your API keys" className="space-y-3">
          {keys.data.map((key) => (
            <li key={key.id} className="plate flex flex-wrap items-center justify-between gap-3 p-4">
              <div className="min-w-0">
                <p className="flex items-center gap-2 font-extrabold">
                  <KeyRound className="size-4 shrink-0" aria-hidden="true" />
                  <span className="truncate">{key.name}</span>
                  {key.revoked_at && (
                    <span className="rounded-full border-2 border-stop px-2 py-0.5 text-xs font-bold text-stop">Revoked</span>
                  )}
                </p>
                <p className="tabular text-sm text-muted-foreground">{key.prefix}…</p>
                <p className="text-xs text-muted-foreground">
                  Created {formatDateTime(key.created_at)} · Last used{" "}
                  {key.last_used_at ? formatDateTime(key.last_used_at) : "never"}
                  {key.revoked_at && <> · Revoked {formatDateTime(key.revoked_at)}</>}
                </p>
              </div>
              {!key.revoked_at && (
                <Button
                  variant="destructive"
                  size="sm"
                  aria-label={`Revoke ${key.name}`}
                  onClick={() => {
                    setRevokeError(undefined);
                    setDialog({ kind: "revoke", key });
                  }}
                >
                  <Trash2 aria-hidden="true" /> Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      <Dialog open={dialog?.kind === "create" && !created} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent title="New API key" description="Give it a name that tells you where it is used.">
          <NewKeyForm onCancel={() => setDialog(null)} create={create} />
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(created)} onOpenChange={(open) => !open && closeCreated()}>
        <DialogContent
          title="Copy your key now"
          description="This is the only time the full key is shown. LinkForge keeps only a fingerprint."
          dismissible={false}
        >
          {created && (
            <div className="space-y-4">
              <p className="tabular break-all rounded-lg border-2 border-line bg-muted p-3 text-sm font-bold select-all">
                {created.key}
              </p>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CopyButton text={created.key} label="API key" />
                <Button onClick={closeCreated}>I have saved it</Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={dialog?.kind === "revoke"} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent title="Revoke this key?" description="This cannot be undone.">
          {dialog?.kind === "revoke" && (
            <div className="space-y-4">
              <p>
                Anything using <span className="font-bold">{dialog.key.name}</span> stops working right away.
              </p>
              {revokeError && <FormMessage tone="error">{revokeError}</FormMessage>}
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setDialog(null)}>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  disabled={revoke.isPending}
                  onClick={() => {
                    const target = dialog.key;
                    setRevokeError(undefined);
                    revoke.mutate(target.id, {
                      onSuccess: () => {
                        setDialog(null);
                        setNotice(`Key ${target.name} revoked.`);
                      },
                      onError: (error) => setRevokeError(describeError(error)),
                    });
                  }}
                >
                  {revoke.isPending ? "Revoking…" : "Revoke key"}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}

function NewKeyForm({ onCancel, create }: { onCancel: () => void; create: ReturnType<typeof useCreateApiKey> }) {
  const [formError, setFormError] = useState<string>();
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<{ name: string }>({ defaultValues: { name: "" } });

  const onSubmit = handleSubmit(({ name }) => {
    setFormError(undefined);
    create.mutate(name.trim(), { onError: (error) => setFormError(applyApiErrors(error, setError, ["name"])) });
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {formError && <FormMessage tone="error">{formError}</FormMessage>}
      <TextField
        label="Name"
        autoComplete="off"
        placeholder="Deploy script"
        hint="Up to 64 characters."
        error={errors.name?.message}
        {...register("name", {
          validate: (v) =>
            v.trim().length === 0 ? "Enter a name." : v.trim().length > 64 ? "Use at most 64 characters." : true,
        })}
      />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create key"}
        </Button>
      </div>
    </form>
  );
}

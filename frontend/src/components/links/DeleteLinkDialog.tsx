import { useState } from "react";
import type { Link } from "@/api/types";
import { FormMessage } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";
import { useDeleteLink } from "@/hooks/useLinks";
import { describeError } from "@/lib/forms";

export function DeleteLinkDialog({
  link,
  onDone,
  onCancel,
}: {
  link: Link;
  onDone: () => void;
  onCancel: () => void;
}) {
  const remove = useDeleteLink();
  const [error, setError] = useState<string>();

  return (
    <div className="space-y-4">
      <p>
        <span className="tabular font-bold">{link.short_url}</span> stops working: anyone who opens it
        gets a 404.
      </p>
      {error && <FormMessage tone="error">{error}</FormMessage>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          disabled={remove.isPending}
          onClick={() => {
            setError(undefined);
            remove.mutate(link.id, { onSuccess: onDone, onError: (e) => setError(describeError(e)) });
          }}
        >
          {remove.isPending ? "Deleting…" : "Delete link"}
        </Button>
      </div>
    </div>
  );
}

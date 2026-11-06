import { useEffect, useState } from "react";
import { Plus, Search } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import type { Link, LinkStatus } from "@/api/types";
import { DeleteLinkDialog } from "@/components/links/DeleteLinkDialog";
import { LinkForm } from "@/components/links/LinkForm";
import { LinkRow } from "@/components/links/LinkRow";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { useLinks, useUpdateLink } from "@/hooks/useLinks";
import { describeError } from "@/lib/forms";
import { formatCount } from "@/lib/format";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const PAGE_SIZE = 20;
const SEARCH_DELAY_MS = 300;
const STATUSES: LinkStatus[] = ["active", "disabled", "expired"];

const parseStatus = (v: string | null): LinkStatus | undefined => STATUSES.find((s) => s === v);
const parsePage = (v: string | null) => {
  const n = Number.parseInt(v ?? "", 10);
  return Number.isInteger(n) && n >= 1 ? n : 1;
};

type DialogState = { kind: "create" } | { kind: "edit"; link: Link } | { kind: "delete"; link: Link } | null;

export default function Links() {
  useDocumentTitle("Links");
  // Filters and page live in the URL: reload, back and shared links all keep the view.
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const status = parseStatus(params.get("status"));
  const page = parsePage(params.get("page"));

  const list = useLinks({ q, status, page, pageSize: PAGE_SIZE });
  const update = useUpdateLink();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string }>();

  const [search, setSearch] = useState(q);
  const [searchKey, setSearchKey] = useState(0);

  const change = (patch: { q?: string; status?: LinkStatus | ""; page?: number }) => {
    const next = new URLSearchParams(params);
    const apply = (key: string, value: string | undefined) =>
      value ? next.set(key, value) : next.delete(key);
    if (patch.q !== undefined) apply("q", patch.q.trim());
    if (patch.status !== undefined) apply("status", patch.status);
    // Any filter change starts again from the first page.
    apply("page", patch.page && patch.page > 1 ? String(patch.page) : undefined);
    setParams(next, { replace: true });
  };

  // Debounced search: the URL (and so the request) follows the box 300 ms after the last keystroke.
  useEffect(() => {
    if (search.trim() === q) return;
    const timer = setTimeout(() => change({ q: search }), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `change` is rebuilt every render; only the text matters
  }, [search, q]);

  const data = list.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  // After deleting the last link of the last page, the page no longer exists: step back.
  useEffect(() => {
    if (data && page > pages) change({ page: pages });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, page, pages]);

  const filtered = q !== "" || status !== undefined;
  const clearFilters = () => {
    setSearch("");
    setSearchKey((k) => k + 1);
    setParams(new URLSearchParams(), { replace: true });
  };

  const toggle = (link: Link) => {
    setNotice(undefined);
    update.mutate(
      { id: link.id, body: { is_active: !link.is_active } },
      {
        onSuccess: () => setNotice({ tone: "ok", text: link.is_active ? "Link disabled." : "Link enabled." }),
        onError: (error) => setNotice({ tone: "error", text: describeError(error) }),
      },
    );
  };

  const closeWith = (text: string) => {
    setDialog(null);
    setNotice({ tone: "ok", text });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-3xl font-extrabold tracking-tight">Links</h1>
        <Button onClick={() => setDialog({ kind: "create" })}>
          <Plus aria-hidden="true" /> New link
        </Button>
      </div>

      <form
        role="search"
        aria-label="Filter links"
        onSubmit={(e) => e.preventDefault()}
        className="flex flex-wrap items-end gap-3"
      >
        <div className="min-w-56 flex-1 space-y-1.5">
          <Label htmlFor="link-search">Search</Label>
          <div className="relative">
            <Search aria-hidden="true" className="absolute top-3 left-3 size-4 text-muted-foreground" />
            <Input
              key={searchKey}
              id="link-search"
              type="search"
              className="pl-9"
              placeholder="Title, code or destination"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="link-status">Status</Label>
          <select
            id="link-status"
            value={status ?? ""}
            onChange={(e) => change({ status: e.target.value as LinkStatus | "" })}
            className="h-10 w-40 rounded-lg border-2 border-line bg-plate px-3 text-sm"
          >
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
            <option value="expired">Expired</option>
          </select>
        </div>
      </form>

      <p role="status" aria-live="polite" className={notice ? "" : "sr-only"}>
        {notice && (
          <span className={notice.tone === "ok" ? "font-bold text-go" : "font-bold text-stop"}>{notice.text}</span>
        )}
      </p>

      {list.isPending ? (
        <LoadingRows label="Loading links" />
      ) : list.isError ? (
        <ErrorState message={describeError(list.error)} onRetry={() => void list.refetch()} />
      ) : data && data.items.length === 0 ? (
        filtered ? (
          <EmptyState
            title="No links match"
            body="Nothing fits this search and status. Try different words, or show everything."
            action={
              <Button variant="secondary" onClick={clearFilters}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            title="No links yet"
            body="Paste a long URL and get a short one you can share. Its clicks show up here."
            action={
              <Button onClick={() => setDialog({ kind: "create" })}>
                <Plus aria-hidden="true" /> Create your first link
              </Button>
            }
          />
        )
      ) : data ? (
        <>
          <ul aria-label="Your links" className="space-y-3">
            {data.items.map((link) => (
              <LinkRow
                key={link.id}
                link={link}
                busy={update.isPending && update.variables?.id === link.id}
                onEdit={() => setDialog({ kind: "edit", link })}
                onToggle={() => toggle(link)}
                onDelete={() => setDialog({ kind: "delete", link })}
              />
            ))}
          </ul>
          <nav aria-label="Pages" className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {formatCount(data.total)} {data.total === 1 ? "link" : "links"} · Page {page} of {pages}
            </p>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => change({ page: page - 1 })}>
                Previous
              </Button>
              <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => change({ page: page + 1 })}>
                Next
              </Button>
            </div>
          </nav>
        </>
      ) : null}

      <Dialog open={dialog?.kind === "create"} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent title="New link" description="Paste the long URL. Alias, title and expiry are optional.">
          <LinkForm onDone={() => closeWith("Link created.")} onCancel={() => setDialog(null)} />
        </DialogContent>
      </Dialog>

      <Dialog open={dialog?.kind === "edit"} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent title="Edit link" description="Change where it goes, its title or when it expires.">
          {dialog?.kind === "edit" && (
            <LinkForm link={dialog.link} onDone={() => closeWith("Link updated.")} onCancel={() => setDialog(null)} />
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={dialog?.kind === "delete"} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent title="Delete this link?" description="This cannot be undone.">
          {dialog?.kind === "delete" && (
            <DeleteLinkDialog link={dialog.link} onDone={() => closeWith("Link deleted.")} onCancel={() => setDialog(null)} />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

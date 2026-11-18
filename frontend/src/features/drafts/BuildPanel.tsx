import { Download, Loader2 } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError, QUIET } from "@/api/client";
import { Button } from "@/components/ui/button";
import { buildDraftPdf, draftError } from "./api";

/** 503: no build service on this server, which the author can't fix themselves. */
const buildError = (e: unknown) => (e instanceof ApiError && e.status === 503 ? "PDF building isn't set up on this server. Ask an admin to turn on the build service." : draftError(e));
const DraftPdf = lazy(() => import("./DraftPdf"));
const slug = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "draft";

/** Build is synchronous (up to ~120 s) and not stored: preview the returned bytes, download saves the same blob. */
export function BuildPanel({ id, title, author }: { id: string; title: string; author: boolean }) {
  const build = useMutation({ meta: QUIET, mutationFn: () => buildDraftPdf(id) });
  const blob = build.data;
  const [href, setHref] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) return setHref(null);
    const u = URL.createObjectURL(blob);
    setHref(u);
    return () => URL.revokeObjectURL(u);
  }, [blob]);
  if (!author) return <div className="dpanel"><p className="sub">Only the owner and co-authors can build a preview.</p></div>;
  return (
    <div className="dpanel">
      <div className="row2">
        <Button onClick={() => build.mutate()} disabled={build.isPending}>{build.isPending && <Loader2 className="spin" aria-hidden />}Build PDF</Button>
        {href && <Button asChild><a href={href} download={`${slug(title)}.pdf`}><Download aria-hidden />Download PDF</a></Button>}
      </div>
      {build.isPending && <p className="sub" role="status">Building the PDF. This can take up to two minutes.</p>}
      {build.isError && <p role="alert" className="derr">{buildError(build.error)}</p>}
      {blob && <Suspense fallback={<p className="sub" role="status">Loading preview…</p>}><DraftPdf blob={blob} /></Suspense>}
    </div>
  );
}

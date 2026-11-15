import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

export function DuplicateDialog({ file, onAdd, onCancel }: { file: File | null; onAdd: () => void; onCancel: () => void }) {
  return (
    <Dialog open={!!file} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent>
        <DialogTitle>Possible duplicate</DialogTitle>
        <DialogDescription>You already have access to this document. Add it anyway?</DialogDescription>
        {file && <p className="vis" style={{ margin: 0 }} title={file.name}>{file.name}</p>}
        <div className="foot">
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" onClick={onAdd}>Add anyway</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

import { Dialog } from "@/components/Overlay";
import { Kbd } from "@/components/Kbd";

const ROWS: [string[], string][] = [
  [["mod", "K"], "Command palette"],
  [["/"], "Focus the page's search field"],
  [["?"], "This help"],
  [["g", "o"], "Go to Overview (then l, j, r, m, d, c, s for the other pages)"],
  [["n"], "New lab / Pull a model / Add host (on that page)"],
  [["r"], "Run… (lab page)"],
  [["e"], "Quick eval…"],
  [["mod", "S"], "Save lab (editor)"],
  [["mod", "Enter"], "Primary action of the open dialog"],
  [["Esc"], "Close dialog, drawer or palette; clear search"],
  [["j"], "Next row in a table (k: previous)"],
  [["Enter"], "Open the focused row"],
  [["["], "Previous tab (]: next)"],
  [["f"], "Toggle Follow in logs"],
  [["t"], "Toggle theme"],
  [["mod", "\\"], "Toggle sidebar"],
];

export function ShortcutHelp({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Keyboard shortcuts" size="md" data-testid="shortcut-help">
      <p className="mb-3 text-small text-muted">Single-key shortcuts are off while you type in a field or the editor.</p>
      <table className="w-full text-body">
        <caption className="sr-only">Keyboard shortcuts</caption>
        <tbody>
          {ROWS.map(([keys, what]) => (
            <tr key={what} className="border-t border-border">
              <td className="whitespace-nowrap py-2 pr-4">
                <Kbd keys={keys} />
              </td>
              <td className="py-2 text-muted">{what}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Dialog>
  );
}

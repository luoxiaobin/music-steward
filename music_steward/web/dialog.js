// In-page confirm / message dialogs. The browser's own confirm() and alert() are blocked in
// some embedded browsers (they return "No" instantly without showing anything) and in some
// desktop shells, so every question the app asks goes through these instead.
function _dialog(message, buttons) {
  return new Promise(resolve => {
    const dlg = document.createElement("dialog");
    dlg.className = "appdialog";
    const p = document.createElement("p");
    p.textContent = message;
    const row = document.createElement("div");
    row.className = "btnrow";
    for (const [label, value, primary] of buttons) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn" + (primary ? " btn-primary" : "");
      b.textContent = label;
      b.addEventListener("click", () => { dlg.close(); dlg.remove(); resolve(value); });
      row.append(b);
    }
    dlg.addEventListener("cancel", e => { e.preventDefault(); dlg.close(); dlg.remove(); resolve(false); });
    dlg.append(p, row);
    document.body.append(dlg);
    dlg.showModal();
    row.lastChild.focus();
  });
}
const ask = (message, yes = "OK") => _dialog(message, [["Cancel", false], [yes, true, true]]);
const tell = message => _dialog(message, [["OK", true, true]]);

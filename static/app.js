const form = document.getElementById("ask-form");
const log = document.getElementById("chat-log");
const input = document.getElementById("question");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = input.value.trim();
  if (!question) return;

  const body = new URLSearchParams({ question });
  input.value = "";
  input.disabled = true;

  const emptyNote = log.querySelector(".empty-note");
  if (emptyNote) emptyNote.remove();

  const res = await fetch("/api/ask", { method: "POST", body });
  const html = await res.text();
  log.insertAdjacentHTML("beforeend", html);
  log.scrollTop = log.scrollHeight;
  input.disabled = false;
  input.focus();
});

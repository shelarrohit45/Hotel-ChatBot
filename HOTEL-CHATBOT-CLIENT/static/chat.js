const thread = document.getElementById("thread");
const conversation = document.querySelector(".conversation");
const form = document.getElementById("chat-form");
const input = document.getElementById("message");
const send = document.getElementById("send");
const gate = document.getElementById("gate");
const gateError = document.getElementById("gate-error");
const guestForm = document.getElementById("guest-form");
const account = document.getElementById("account");
const accountName = document.getElementById("account-name");
const logoutBtn = document.getElementById("logout");
const lightbox = document.getElementById("lightbox");
const lightboxImage = document.getElementById("lightbox-image");
const lightboxCaption = document.getElementById("lightbox-caption");
const lightboxClose = document.getElementById("lightbox-close");

let ready = false;

function addMessage(role, text, images) {
  const wrap = document.createElement("article");
  wrap.className = "msg " + role;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = role === "user" ? "You" : "Desk";
  const body = document.createElement("div");
  body.className = "copy";
  body.textContent = text;
  wrap.appendChild(who);
  wrap.appendChild(body);
  if (images && images.length) {
    const gallery = document.createElement("div");
    gallery.className = "photos";
    images.forEach((item) => {
      const url = typeof item === "string" ? item : item && item.url;
      if (!url) return;
      const caption = (item && item.caption) || "Hotel";
      const figure = document.createElement("figure");
      const img = document.createElement("img");
      img.src = url;
      img.alt = caption;
      img.loading = "lazy";
      img.addEventListener("click", () => openLightbox(url, caption));
      figure.appendChild(img);
      if (caption && caption !== "Hotel") {
        const cap = document.createElement("figcaption");
        cap.textContent = caption;
        figure.appendChild(cap);
      }
      gallery.appendChild(figure);
    });
    if (gallery.childElementCount) wrap.appendChild(gallery);
  }
  thread.appendChild(wrap);
  const pane = conversation || thread;
  pane.scrollTop = pane.scrollHeight;
  return wrap;
}

function setBusy(busy) {
  send.disabled = busy || !ready;
  input.disabled = busy || !ready;
}

function showGate(message) {
  ready = false;
  setBusy(false);
  document.body.classList.add("locked");
  gate.hidden = false;
  account.hidden = true;
  if (gateError) gateError.textContent = message || "";
}

function enterDesk(user) {
  ready = true;
  document.body.classList.remove("locked");
  gate.hidden = true;
  account.hidden = false;
  accountName.textContent = user.name || user.email;
  setBusy(false);
  input.focus();
}

function growInput() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}

async function loadSession() {
  const response = await fetch("/auth/me", { credentials: "same-origin" });
  const data = await response.json();
  if (data.user && data.user.name && data.user.email && data.user.phone) {
    enterDesk(data.user);
    return;
  }
  showGate();
}

async function sendMessage(text) {
  const message = (text || "").trim();
  if (!message || !ready) return;
  addMessage("user", message);
  input.value = "";
  growInput();
  setBusy(true);
  const pending = addMessage("pending", "One moment…");
  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ message }),
    });
    const data = await response.json();
    pending.remove();
    if (response.status === 401) {
      showGate(data.error || "Enter your details first.");
      return;
    }
    if (!response.ok) {
      addMessage("assistant", data.error || "The desk is unavailable right now.");
      return;
    }
    addMessage("assistant", data.text || "No reply.", data.images || []);
  } catch (err) {
    pending.remove();
    addMessage("assistant", "Could not reach the desk.");
  } finally {
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(input.value);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage(input.value);
  }
});

input.addEventListener("input", growInput);

guestForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  gateError.textContent = "";
  const response = await fetch("/auth/guest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({
      name: document.getElementById("guest-name").value,
      email: document.getElementById("guest-email").value,
      phone: document.getElementById("guest-phone").value,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    gateError.textContent = data.error || "Could not save those details.";
    return;
  }
  enterDesk(data.user);
});

logoutBtn.addEventListener("click", async () => {
  await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  thread.replaceChildren();
  guestForm.reset();
  showGate();
});

function openLightbox(url, caption) {
  if (!lightbox || !lightboxImage) return;
  lightboxImage.src = url;
  lightboxImage.alt = caption || "Hotel";
  lightboxCaption.textContent = caption || "";
  lightbox.hidden = false;
}

function closeLightbox() {
  if (!lightbox) return;
  lightbox.hidden = true;
  lightboxImage.src = "";
}

lightboxClose.addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (event) => {
  if (event.target === lightbox) closeLightbox();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && lightbox && !lightbox.hidden) closeLightbox();
});

setBusy(true);
loadSession().catch(() => {
  showGate("Could not check guest profile.");
});

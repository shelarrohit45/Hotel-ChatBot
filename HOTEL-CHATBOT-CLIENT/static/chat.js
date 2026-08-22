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
const payToast = document.getElementById("pay-toast");
const payCard = payToast ? payToast.querySelector(".pay-card") : null;
const payTitle = document.getElementById("pay-title");
const payCopy = document.getElementById("pay-copy");
const payOk = document.getElementById("pay-toast-ok");

let ready = false;
let paying = false;

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
    const wrap = addMessage("assistant", data.text || "No reply.", data.images || []);
    const bookingId = data.pay_booking_id || (data.payment && data.payment.booking_id);
    if (bookingId) {
      addPayButton(wrap, bookingId, data.pay_amount_inr, data.payment);
      if (data.payment && data.payment.order_id) {
        openCheckout(data.payment);
      } else {
        showPayToast(
          false,
          "Payment keys missing",
          "Add Razorpay Test Key Id and Key Secret to HOTEL-CHATBOT-CLIENT/.env, restart the server, then tap Pay now."
        );
      }
    }
    if (data.receipts && data.receipts.length) {
      addReceipts(wrap, data.receipts);
    }
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

function showPayToast(ok, title, copy) {
  if (!payToast || !payCard || !payTitle || !payCopy) return;
  payCard.className = "pay-card " + (ok ? "ok" : "bad");
  payTitle.textContent = title;
  payCopy.textContent = copy;
  payToast.hidden = false;
}

function hidePayToast() {
  if (!payToast) return;
  payToast.hidden = true;
  input.focus();
}

if (payOk) payOk.addEventListener("click", hidePayToast);
if (payToast) {
  payToast.addEventListener("click", (event) => {
    if (event.target === payToast) hidePayToast();
  });
}

function addReceipts(wrap, receipts) {
  receipts.forEach((item) => {
    const card = document.createElement("div");
    card.className = "receipt";
    const title = document.createElement("p");
    title.className = "receipt-kicker";
    title.textContent = "Payment receipt";
    const hotel = document.createElement("h3");
    hotel.textContent = item.hotel_name || "Hotel stay";
    const meta = document.createElement("p");
    meta.className = "receipt-meta";
    meta.textContent = [
      item.booking_id,
      item.payment_id,
      item.total_inr != null ? "INR " + item.total_inr : "",
      item.check_in && item.check_out ? item.check_in + " → " + item.check_out : "",
    ].filter(Boolean).join(" · ");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pay-now";
    button.textContent = "Download receipt";
    button.addEventListener("click", () => downloadReceipt(item.booking_id));
    card.appendChild(title);
    card.appendChild(hotel);
    card.appendChild(meta);
    card.appendChild(button);
    wrap.appendChild(card);
  });
  const pane = conversation || thread;
  pane.scrollTop = pane.scrollHeight;
}

async function downloadReceipt(bookingId) {
  try {
    const response = await fetch("/receipt/" + encodeURIComponent(bookingId), {
      credentials: "same-origin",
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      showPayToast(false, "Receipt unavailable", data.error || "Could not download that receipt.");
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "receipt-" + bookingId + ".html";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showPayToast(false, "Receipt unavailable", "Could not download that receipt.");
  }
}

function addPayButton(wrap, bookingId, amountInr, payment) {
  const row = document.createElement("div");
  row.className = "pay-row";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "pay-now";
  const rupees = amountInr ? "Pay ₹" + amountInr : "Pay now";
  button.textContent = rupees;
  button.addEventListener("click", async () => {
    if (paying) return;
    button.disabled = true;
    try {
      if (payment && payment.order_id) {
        openCheckout(payment);
        return;
      }
      const response = await fetch("/pay/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ booking_id: bookingId }),
      });
      const data = await response.json();
      if (!response.ok || !data.payment) {
        showPayToast(false, "Payment unavailable", data.error || "Could not open Razorpay.");
        return;
      }
      openCheckout(data.payment);
    } finally {
      button.disabled = false;
    }
  });
  row.appendChild(button);
  wrap.appendChild(row);
  const pane = conversation || thread;
  pane.scrollTop = pane.scrollHeight;
}

function openCheckout(payment) {
  if (typeof Razorpay !== "function") {
    showPayToast(false, "Payment unavailable", "The payment window could not load. Refresh and try booking again.");
    return;
  }
  if (paying) return;
  paying = true;
  let success = false;
  const options = {
    key: payment.key_id,
    amount: payment.amount,
    currency: payment.currency || "INR",
    name: "The Desk",
    description: payment.description || "Hotel stay",
    order_id: payment.order_id,
    prefill: payment.prefill || {},
    theme: { color: "#0e0d0b" },
    modal: {
      ondismiss: function () {
        window.setTimeout(function () {
          if (success) return;
          paying = false;
          markPayFailed(payment.booking_id, "Payment window closed", payment.order_id, "");
          showPayToast(false, "Payment failed", "The payment was cancelled. You are back on the chat. Say book again if you still want the stay.");
        }, 500);
      },
    },
    handler: function (response) {
      success = true;
      paying = false;
      confirmPay(payment.booking_id, response);
    },
  };
  const checkout = new Razorpay(options);
  checkout.on("payment.failed", function (response) {
    success = true;
    paying = false;
    const reason = (response && response.error && response.error.description) || "Payment failed";
    const failedId = response && response.error && response.error.metadata
      ? response.error.metadata.payment_id
      : "";
    markPayFailed(payment.booking_id, reason, payment.order_id, failedId);
    showPayToast(false, "Payment failed", reason + " You are back on the chat.");
  });
  checkout.open();
}

async function confirmPay(bookingId, response) {
  try {
    const result = await fetch("/pay/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        booking_id: bookingId,
        razorpay_order_id: response.razorpay_order_id,
        razorpay_payment_id: response.razorpay_payment_id,
        razorpay_signature: response.razorpay_signature,
      }),
    });
    const data = await result.json();
    if (!result.ok || !data.ok) {
      showPayToast(false, "Payment failed", data.error || "The payment could not be confirmed. You are back on the chat.");
      return;
    }
    const paid = addMessage("assistant", "Payment received. Booking " + (data.booking_id || "") + " is confirmed.");
    if (data.receipt) addReceipts(paid, [data.receipt]);
  } catch (err) {
    showPayToast(false, "Payment failed", "Could not confirm the payment. You are back on the chat.");
  }
}

async function markPayFailed(bookingId, reason, orderId, paymentId) {
  try {
    await fetch("/pay/fail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        booking_id: bookingId,
        reason: reason || "",
        razorpay_order_id: orderId || "",
        razorpay_payment_id: paymentId || "",
      }),
    });
  } catch (err) {
    /* stay on chat even if the fail ping does not land */
  }
}

setBusy(true);
loadSession().catch(() => {
  showGate("Could not check guest profile.");
});

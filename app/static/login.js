"use strict";
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("owner-login");
  const message = document.getElementById("login-message");
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    message.textContent = "Checking access…";
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({secret: form.elements.secret.value}),
      });
      const result = await response.json();
      if (!response.ok || !result.authenticated) throw new Error(result.error || "Access denied");
      location.replace("/");
    } catch (error) {
      message.textContent = error.message;
      form.elements.secret.value = "";
      form.elements.secret.focus();
      button.disabled = false;
    }
  });
});

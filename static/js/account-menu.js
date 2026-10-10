// Close the header account menu (<details data-account-menu>) on outside click or Escape.
document.querySelectorAll("[data-account-menu]").forEach((menu) => {
  document.addEventListener("click", (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.open) {
      menu.open = false;
      menu.querySelector("summary")?.focus();
    }
  });
});

// Product image gallery: thumbnails, prev/next buttons and arrow keys.
document.querySelectorAll("[data-gallery]").forEach((gallery) => {
  const main = gallery.querySelector("[data-gallery-main]");
  const counter = gallery.querySelector("[data-gallery-counter]");
  const thumbs = [...gallery.querySelectorAll("[data-gallery-thumb]")];
  if (!main || thumbs.length < 2) return;

  let current = 0;

  const show = (index) => {
    current = (index + thumbs.length) % thumbs.length;
    const thumb = thumbs[current];
    main.src = thumb.dataset.src;
    main.alt = thumb.dataset.alt;
    thumbs.forEach((t, i) => t.setAttribute("aria-current", String(i === current)));
    if (counter) counter.textContent = `${current + 1} / ${thumbs.length}`;
    thumb.scrollIntoView({ block: "nearest", inline: "nearest" });
  };

  thumbs.forEach((thumb, i) => thumb.addEventListener("click", () => show(i)));
  gallery.querySelector("[data-gallery-prev]")?.addEventListener("click", () => show(current - 1));
  gallery.querySelector("[data-gallery-next]")?.addEventListener("click", () => show(current + 1));
  gallery.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") show(current - 1);
    else if (event.key === "ArrowRight") show(current + 1);
    else return;
    event.preventDefault();
  });

  // Preload the rest so switching is instant.
  thumbs.slice(1).forEach((t) => { new Image().src = t.dataset.src; });
});

// Soteria landing page: navigation state, mobile menu, and entrance reveals.
// The page reads fine without this file; it only adds polish.

const nav = document.querySelector(".nav");
const toggle = document.querySelector(".nav__toggle");
const menu = document.getElementById("menu");

// Solid navigation once the visitor scrolls past the top of the hero.
const onScroll = () => nav.classList.toggle("is-solid", window.scrollY > 24);
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();

// Mobile menu.
function setMenu(open) {
  menu.hidden = !open;
  nav.classList.toggle("is-open", open);
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  document.body.style.overflow = open ? "hidden" : "";
}
toggle.addEventListener("click", () => setMenu(menu.hidden));
menu.addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !menu.hidden) { setMenu(false); toggle.focus(); }
});
window.matchMedia("(min-width: 768px)").addEventListener("change", (e) => { if (e.matches) setMenu(false); });

// Fade the hero photograph in once it has loaded.
const heroImg = document.querySelector(".hero__img");
const showHero = () => heroImg.classList.add("is-loaded");
if (heroImg.complete) showHero();
else { heroImg.addEventListener("load", showHero); heroImg.addEventListener("error", showHero); }

// Reveal sections as they enter the viewport (the CSS ignores this under reduced motion).
const revealables = document.querySelectorAll("[data-reveal]");
if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("is-in");
      observer.unobserve(entry.target);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
  revealables.forEach((node) => observer.observe(node));
} else {
  revealables.forEach((node) => node.classList.add("is-in"));
}

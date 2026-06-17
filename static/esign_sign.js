// ② SES signing — vanilla, CSP-safe (script-src 'self'). Lets the signer DRAW a
// signature on the <canvas>; on submit the drawing is serialized to a PNG data URL and
// posted in the hidden #esign-image field. Drawing is OPTIONAL (a typed name is the
// signature); this only enriches the audit record. No external dependencies.
(function () {
  "use strict";
  const canvas = document.getElementById("esign-canvas");
  const form = document.getElementById("esign-form");
  const imageField = document.getElementById("esign-image");
  const clearBtn = document.getElementById("esign-clear");
  if (!canvas || !form || !imageField) return;

  const ctx = canvas.getContext("2d");
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#10243a";

  let drawing = false;
  let dirty = false;

  function pos(ev) {
    const rect = canvas.getBoundingClientRect();
    const src = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return { x: (src.clientX - rect.left) * scaleX, y: (src.clientY - rect.top) * scaleY };
  }

  function start(ev) {
    ev.preventDefault();
    drawing = true;
    dirty = true;
    const p = pos(ev);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
  }

  function move(ev) {
    if (!drawing) return;
    ev.preventDefault();
    const p = pos(ev);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  }

  function end(ev) {
    if (ev) ev.preventDefault();
    drawing = false;
  }

  canvas.addEventListener("mousedown", start);
  canvas.addEventListener("mousemove", move);
  window.addEventListener("mouseup", end);
  canvas.addEventListener("touchstart", start, { passive: false });
  canvas.addEventListener("touchmove", move, { passive: false });
  canvas.addEventListener("touchend", end, { passive: false });

  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      dirty = false;
      imageField.value = "";
    });
  }

  form.addEventListener("submit", function () {
    try {
      imageField.value = dirty ? canvas.toDataURL("image/png") : "";
    } catch (e) {
      imageField.value = "";
    }
  });
})();

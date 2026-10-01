document
  .querySelectorAll(".notice-close")
  .forEach((button) =>
    button.addEventListener("click", () => button.closest(".notice").remove()),
  );
document.querySelectorAll("[data-confirm]").forEach((form) =>
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }),
);
document
  .querySelectorAll("[data-print]")
  .forEach((button) => button.addEventListener("click", () => window.print()));
document.querySelectorAll("[data-password]").forEach((button) =>
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.password);
    const visible = input.type === "password";
    input.type = visible ? "text" : "password";
    button.textContent = visible ? "Скрыть" : "Показать";
    button.setAttribute(
      "aria-label",
      visible ? "Скрыть пароль" : "Показать пароль",
    );
  }),
);
const seatForm = document.getElementById("seat-form");
if (seatForm) {
  const selected = new Map();
  const maximum = Number(seatForm.dataset.max);
  const price = Number(seatForm.dataset.price);
  const status = document.getElementById("seat-status");
  const input = document.getElementById("selected-seats");
  const labels = document.getElementById("selected-seat-labels");
  const bookButton = document.getElementById("book-button");
  const render = () => {
    const seats = [...selected.values()].sort(
      (a, b) => a.row - b.row || a.number - b.number,
    );
    input.value = seats.map((seat) => `${seat.row}-${seat.number}`).join(",");
    document.getElementById("seat-count").textContent =
      `${seats.length} / ${maximum}`;
    document.getElementById("booking-total").textContent =
      `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(seats.length * price)} ₽`;
    labels.replaceChildren();
    if (!seats.length) {
      const message = document.createElement("p");
      message.className = "muted";
      message.textContent = "Нажмите на свободное место на схеме";
      labels.append(message);
    }
    seats.forEach((seat) => {
      const label = document.createElement("span");
      label.className = "selected-seat-chip";
      label.textContent = `${seat.row} ряд · ${seat.number} место`;
      labels.append(label);
    });
    bookButton.disabled = seats.length === 0;
  };
  seatForm.querySelectorAll(".seat:not(:disabled)").forEach((button) =>
    button.addEventListener("click", () => {
      const row = Number(button.dataset.row);
      const number = Number(button.dataset.number);
      const key = `${row}-${number}`;
      status.textContent = "";
      if (selected.has(key)) selected.delete(key);
      else if (selected.size < maximum) selected.set(key, { row, number });
      else {
        status.textContent = `В одной брони может быть не больше ${maximum} мест.`;
        return;
      }
      button.classList.toggle("selected", selected.has(key));
      button.setAttribute("aria-pressed", String(selected.has(key)));
      render();
    }),
  );
  seatForm.addEventListener("submit", (event) => {
    if (!selected.size) {
      event.preventDefault();
      status.textContent = "Сначала выберите хотя бы одно место.";
    } else {
      bookButton.disabled = true;
      bookButton.textContent = "Бронируем…";
    }
  });
  window.addEventListener("pageshow", render);
}

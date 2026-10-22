document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("delete-selected");
  if (form) {
    var pageBox = document.getElementById("select-page");
    var button = document.getElementById("delete-selected-button");
    var count = document.getElementById("selected-count");
    var rows = function () { return Array.prototype.slice.call(form.querySelectorAll(".row-select")); };
    var refresh = function () {
      var boxes = rows();
      var chosen = boxes.filter(function (box) { return box.checked; }).length;
      if (count) count.textContent = chosen + " selected";
      if (button) button.disabled = chosen === 0;
      if (pageBox) pageBox.checked = boxes.length > 0 && chosen === boxes.length;
    };
    if (pageBox) {
      pageBox.addEventListener("change", function () {
        rows().forEach(function (box) { box.checked = pageBox.checked; });
        refresh();
      });
    }
    form.addEventListener("change", function (event) {
      if (event.target && event.target.classList && event.target.classList.contains("row-select")) refresh();
    });
    refresh();
  }

  var today = new Date().toISOString().slice(0, 10);
  document.querySelectorAll('input[type="date"][name="date_of_birth"]').forEach(function (input) {
    input.max = today;
  });
});

document.addEventListener("input", function (event) {
  if (event.target && event.target.setCustomValidity) event.target.setCustomValidity("");
});

document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!form || !form.querySelectorAll) return;
  var blocked = false;
  form.querySelectorAll("[data-digits-min]").forEach(function (input) {
    var digits = (input.value.match(/\d/g) || []).length;
    var min = Number(input.getAttribute("data-digits-min"));
    var max = Number(input.getAttribute("data-digits-max") || "99");
    if (input.value && (digits < min || digits > max)) {
      input.setCustomValidity("Enter " + min + " to " + max + " digits.");
      blocked = true;
    } else {
      input.setCustomValidity("");
    }
  });
  form.querySelectorAll("[data-mobile]").forEach(function (input) {
    var value = input.value.trim();
    var message = "";
    if (value && !/^[0-9]{10}$/.test(value)) {
      message = "Enter exactly 10 digits. Do not include a country code.";
    } else if (value && (value === "1234567890" || /^(\d)\1{9}$/.test(value) || mobileSequence(value))) {
      message = "Enter a real mobile number. 1234567890 and numbers like 0000000000 are not allowed.";
    } else if (value && !/^[6-9][0-9]{9}$/.test(value)) {
      message = "Enter a valid mobile number starting with 6, 7, 8, or 9.";
    }
    input.setCustomValidity(message);
    if (message) blocked = true;
  });
  form.querySelectorAll("[data-aadhaar]").forEach(function (input) {
    var value = (input.value || "").trim();
    var message = "";
    if (value && !/^[0-9 ]+$/.test(value)) {
      message = "Enter only numbers.";
    } else if (value && value.replace(/ /g, "").length !== 12) {
      message = "Enter a 12-digit Aadhaar number.";
    }
    input.setCustomValidity(message);
    if (message) blocked = true;
  });
  form.querySelectorAll("[data-must-letter]").forEach(function (input) {
    if (input.value && !/\p{L}/u.test(input.value)) {
      input.setCustomValidity("Enter a name that includes letters.");
      blocked = true;
    } else {
      input.setCustomValidity("");
    }
  });
  if (blocked) {
    event.preventDefault();
    form.reportValidity();
  }
});

function mobileSequence(value) {
  var steps = [];
  for (var index = 0; index < value.length - 1; index += 1) {
    steps.push(Number(value[index + 1]) - Number(value[index]));
  }
  return steps.every(function (step) { return step === 1; }) || steps.every(function (step) { return step === -1; });
}

document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy-address]");
  if (!button) return;
  ["line1", "line2", "district", "state", "pin"].forEach(function (name) {
    var source = document.getElementById("present_" + name);
    var target = document.getElementById("permanent_" + name);
    if (source && target) target.value = source.value;
  });
});

document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy-address]");
  if (!button) return;
  ["line1", "line2", "district", "state", "pin"].forEach(function (name) {
    var source = document.getElementById("present_" + name);
    var target = document.getElementById("permanent_" + name);
    if (source && target) target.value = source.value;
  });
});

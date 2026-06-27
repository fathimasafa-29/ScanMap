const API_BASE = "http://localhost:5000";

// ─── State ───────────────────────────────────────────────────────────────────
let formData = {
  invoice: {
    invoiceNumber: "", invoiceDate: "",
    billedTo: "The Training and Placement Cell, College of Engineering Thalassery",
    fromCompany: "", fromEmail: "", fromWebsite: "",
    serviceDescription: "", pricePerSession: 0, totalSessions: 0, totalAmount: 0,
    accommodationCharge: 0,
    accountNumber: "", accountName: "", ifscCode: "", bankName: "",
    startDate: "", endDate: ""
  },
  training: {
    collegeName: "College of Engineering Thalassery",
    department: "", year: "", trainingType: "Coding",
    trainingTopic: "", targetStudents: "",
    startDate: "", endDate: "", numberOfDays: 0, sessionsPerDay: 2,
    sessionTimings: "9:00 AM - 4:00 PM",
    objective: "To provide comprehensive coverage of key skills in solving problems.",
    reportDescription: ""
  },
  schedule: [{ date: "", time: "9:00 AM - 4:00 PM", sessions: 2, topics: "" }]
};

let rawText = "";

// ─── Semester to Year helper ─────────────────────────────────────────────────
function semesterToYear(sem) {
  if (!sem) return "";
  const s = sem.toString().toUpperCase().replace(/[^0-9]/g, "");
  const num = parseInt(s);
  if (!num) return "";
  if (num <= 2) return "1st";
  if (num <= 4) return "2nd";
  if (num <= 6) return "3rd";
  if (num <= 8) return "4th";
  return "";
}

// ─── Page Navigation ─────────────────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.getElementById("page-" + name).classList.add("active");
  if (name !== "home") syncFieldsToPage(name);
  window.scrollTo(0, 0);
}

// ─── File Upload ─────────────────────────────────────────────────────────────
const uploadZone = document.getElementById("upload-zone");
const fileInput = document.getElementById("file-input");

uploadZone.addEventListener("dragover", e => { e.preventDefault(); uploadZone.classList.add("drag-over"); });
uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("drag-over"));
uploadZone.addEventListener("drop", e => {
  e.preventDefault();
  uploadZone.classList.remove("drag-over");
  if (e.dataTransfer.files[0]) processFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", e => {
  if (e.target.files[0]) processFile(e.target.files[0]);
});

async function processFile(file) {
  document.getElementById("upload-title").textContent = `Extracting from ${file.name}...`;
  document.getElementById("upload-subtitle").textContent = "";
  document.querySelector(".upload-icon").textContent = "";
  document.getElementById("upload-zone").innerHTML = `
    <div class="spinner"></div>
    <h3>Extracting data from ${file.name}...</h3>
    <div class="progress-bar"><div class="progress-fill" style="width:60%"></div></div>
    <p style="font-size:13px;color:var(--text-muted)">Processing...</p>
  `;

  const fd = new FormData();
  fd.append("file", file);

  try {
    const res = await fetch(API_BASE + "/api/extract", { method: "POST", body: fd });
    const json = await res.json();

    if (json.error) {
      alert("Error: " + json.error);
      location.reload();
      return;
    }

    rawText = json.rawText || "";
    const inv = json.data;

    // Map to formData
    formData.invoice = { ...formData.invoice, ...inv };
    formData.training.trainingTopic = inv.serviceDescription || "";
    formData.training.numberOfDays = inv.totalSessions || 0;
    formData.training.startDate = inv.startDate || "";
    formData.training.endDate = inv.endDate || "";

    // Extract department and year from target students if available
    if (inv.department) formData.training.department = inv.department;
    if (inv.year) formData.training.year = inv.year;
    else if (inv.semester) formData.training.year = semesterToYear(inv.semester);

    // Build schedule
    const numDays = inv.totalSessions || 1;
    formData.schedule = buildScheduleDays(numDays, inv.startDate);

    // Update UI
    document.getElementById("upload-zone").innerHTML = `
      <input type="file" id="file-input-2" accept="image/*,.pdf" style="position:absolute;inset:0;opacity:0;cursor:pointer" />
      <div class="success-icon">✅</div>
      <h3>Data Extracted Successfully</h3>
      <p style="font-size:13px;color:var(--text-muted)">📄 ${file.name}</p>
    `;
    document.getElementById("file-input-2").addEventListener("change", e => {
      if (e.target.files[0]) { location.reload(); }
    });

    document.getElementById("raw-toggle").style.display = "block";
    document.getElementById("raw-text-box").textContent = rawText;
    document.getElementById("nav-links").style.display = "flex";
    document.getElementById("templates-header").style.display = "flex";
    document.getElementById("template-grid").style.display = "grid";
  } catch (err) {
    alert("Failed to process. Make sure the backend is running.\n" + err.message);
    location.reload();
  }
}

function buildScheduleDays(count, startStr) {
  const days = [];
  const start = tryParseDate(startStr);
  for (let i = 0; i < count; i++) {
    let dateStr = "";
    if (start) {
      const d = new Date(start);
      d.setDate(d.getDate() + i);
      dateStr = formatDate(d);
    }
    days.push({ date: dateStr, time: "9:00 AM - 4:00 PM", sessions: 2, topics: "" });
  }
  return days;
}

function tryParseDate(str) {
  if (!str) return null;
  const dmy = str.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/);
  if (dmy) {
    const y = dmy[3].length <= 2 ? 2000 + parseInt(dmy[3]) : parseInt(dmy[3]);
    return new Date(y, parseInt(dmy[2]) - 1, parseInt(dmy[1]));
  }
  const named = str.match(/^(\d{1,2})\s+(\w+)\s+(\d{4})$/);
  if (named) {
    const d = new Date(named[2] + " " + named[1] + ", " + named[3]);
    if (!isNaN(d)) return d;
  }
  const iso = new Date(str);
  return isNaN(iso) ? null : iso;
}

function formatDate(d) {
  return String(d.getDate()).padStart(2, "0") + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + d.getFullYear();
}

function toggleRawText() {
  document.getElementById("raw-text-box").classList.toggle("show");
}

// ─── Sync Fields ─────────────────────────────────────────────────────────────
function syncFieldsToPage(page) {
  const inv = formData.invoice;
  const trn = formData.training;

  function setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val ?? "";
  }

  if (page === "expenditure") {
    setVal("exp-invoiceNumber", inv.invoiceNumber);
    setVal("exp-invoiceDate", inv.invoiceDate);
    setVal("exp-fromCompany", inv.fromCompany);
    setVal("exp-pricePerSession", inv.pricePerSession);
    setVal("exp-totalSessions", inv.totalSessions);
    setVal("exp-totalAmount", inv.totalAmount);
    setVal("exp-accommodationCharge", inv.accommodationCharge);
    setVal("exp-collegeName", trn.collegeName);
    setVal("exp-department", trn.department);
    setVal("exp-year", trn.year);
    setVal("exp-targetStudents", trn.targetStudents);
    setVal("exp-trainingType", trn.trainingType);
    setVal("exp-numberOfDays", trn.numberOfDays);
    setVal("exp-sessionsPerDay", trn.sessionsPerDay);
    setVal("exp-sessionTimings", trn.sessionTimings);
    setVal("exp-startDate", trn.startDate);
    setVal("exp-endDate", trn.endDate);
    setVal("exp-accountNumber", inv.accountNumber);
    setVal("exp-accountName", inv.accountName);
    setVal("exp-ifscCode", inv.ifscCode);
    setVal("exp-bankName", inv.bankName);
  }
  if (page === "report") {
    setVal("rpt-collegeName", trn.collegeName);
    setVal("rpt-department", trn.department);
    setVal("rpt-year", trn.year);
    setVal("rpt-targetStudents", trn.targetStudents);
    setVal("rpt-trainingType", trn.trainingType);
    setVal("rpt-trainingTopic", trn.trainingTopic);
    setVal("rpt-startDate", trn.startDate);
    setVal("rpt-endDate", trn.endDate);
    setVal("rpt-numberOfDays", trn.numberOfDays);
    setVal("rpt-reportDescription", trn.reportDescription);
  }
  if (page === "schedule") {
    setVal("sch-collegeName", trn.collegeName);
    setVal("sch-department", trn.department);
    setVal("sch-year", trn.year);
    setVal("sch-targetStudents", trn.targetStudents);
    setVal("sch-trainingType", trn.trainingType);
    setVal("sch-trainingTopic", trn.trainingTopic);
    setVal("sch-startDate", trn.startDate);
    setVal("sch-endDate", trn.endDate);
    setVal("sch-numberOfDays", trn.numberOfDays);
    setVal("sch-sessionsPerDay", trn.sessionsPerDay);
    setVal("sch-sessionTimings", trn.sessionTimings);
    setVal("sch-objective", trn.objective);
    renderScheduleDays();
  }
}

// ─── Read fields back ────────────────────────────────────────────────────────
function readFieldsFromPage(page) {
  function getVal(id) {
    const el = document.getElementById(id);
    return el ? el.value : "";
  }
  function getNum(id) {
    return Number(getVal(id)) || 0;
  }

  if (page === "expenditure") {
    formData.invoice.invoiceNumber = getVal("exp-invoiceNumber");
    formData.invoice.invoiceDate = getVal("exp-invoiceDate");
    formData.invoice.fromCompany = getVal("exp-fromCompany");
    formData.invoice.pricePerSession = getNum("exp-pricePerSession");
    formData.invoice.totalSessions = getNum("exp-totalSessions");
    formData.invoice.totalAmount = getNum("exp-totalAmount");
    formData.invoice.accommodationCharge = getNum("exp-accommodationCharge");
    formData.training.collegeName = getVal("exp-collegeName");
    formData.training.department = getVal("exp-department");
    formData.training.year = getVal("exp-year");
    formData.training.targetStudents = getVal("exp-targetStudents");
    formData.training.trainingType = getVal("exp-trainingType");
    formData.training.numberOfDays = getNum("exp-numberOfDays");
    formData.training.sessionsPerDay = getNum("exp-sessionsPerDay");
    formData.training.sessionTimings = getVal("exp-sessionTimings");
    formData.training.startDate = getVal("exp-startDate");
    formData.training.endDate = getVal("exp-endDate");
    formData.invoice.accountNumber = getVal("exp-accountNumber");
    formData.invoice.accountName = getVal("exp-accountName");
    formData.invoice.ifscCode = getVal("exp-ifscCode");
    formData.invoice.bankName = getVal("exp-bankName");
  }
  if (page === "report") {
    formData.training.collegeName = getVal("rpt-collegeName");
    formData.training.department = getVal("rpt-department");
    formData.training.year = getVal("rpt-year");
    formData.training.targetStudents = getVal("rpt-targetStudents");
    formData.training.trainingType = getVal("rpt-trainingType");
    formData.training.trainingTopic = getVal("rpt-trainingTopic");
    formData.training.startDate = getVal("rpt-startDate");
    formData.training.endDate = getVal("rpt-endDate");
    formData.training.numberOfDays = getNum("rpt-numberOfDays");
    formData.training.reportDescription = getVal("rpt-reportDescription");
  }
  if (page === "schedule") {
    formData.training.collegeName = getVal("sch-collegeName");
    formData.training.department = getVal("sch-department");
    formData.training.year = getVal("sch-year");
    formData.training.targetStudents = getVal("sch-targetStudents");
    formData.training.trainingType = getVal("sch-trainingType");
    formData.training.trainingTopic = getVal("sch-trainingTopic");
    formData.training.startDate = getVal("sch-startDate");
    formData.training.endDate = getVal("sch-endDate");
    formData.training.numberOfDays = getNum("sch-numberOfDays");
    formData.training.sessionsPerDay = getNum("sch-sessionsPerDay");
    formData.training.sessionTimings = getVal("sch-sessionTimings");
    formData.training.objective = getVal("sch-objective");
    readScheduleDays();
  }
}

// ─── Schedule Days ───────────────────────────────────────────────────────────
function renderScheduleDays() {
  const container = document.getElementById("schedule-days");
  container.innerHTML = "";
  formData.schedule.forEach((day, i) => {
    const div = document.createElement("div");
    div.className = "schedule-day";
    div.innerHTML = `
      <div class="schedule-day-header">
        <span>Day ${i + 1}</span>
        ${formData.schedule.length > 1 ? `<button class="btn btn-danger" onclick="removeScheduleDay(${i})">🗑 Remove</button>` : ""}
      </div>
      <div class="schedule-grid">
        <div class="field"><label>Date</label><input id="sday-${i}-date" value="${day.date}" /></div>
        <div class="field"><label>Time</label><input id="sday-${i}-time" value="${day.time}" /></div>
        <div class="field"><label>Sessions</label><input type="number" id="sday-${i}-sessions" value="${day.sessions}" /></div>
        <div class="field"><label>Topics</label><input id="sday-${i}-topics" value="${day.topics}" placeholder="Python basics, loops..." /></div>
      </div>
    `;
    container.appendChild(div);
  });
}

function readScheduleDays() {
  formData.schedule = formData.schedule.map((day, i) => ({
    date: document.getElementById(`sday-${i}-date`)?.value || day.date,
    time: document.getElementById(`sday-${i}-time`)?.value || day.time,
    sessions: Number(document.getElementById(`sday-${i}-sessions`)?.value) || day.sessions,
    topics: document.getElementById(`sday-${i}-topics`)?.value || day.topics,
  }));
}

function addScheduleDay() {
  readScheduleDays();
  formData.schedule.push({ date: "", time: "9:00 AM - 4:00 PM", sessions: 2, topics: "" });
  renderScheduleDays();
}

function removeScheduleDay(idx) {
  readScheduleDays();
  formData.schedule.splice(idx, 1);
  renderScheduleDays();
}

// ─── PDF Download ────────────────────────────────────────────────────────────
function getCurrentPage() {
  const pages = ["expenditure", "report", "schedule"];
  for (const p of pages) {
    if (document.getElementById("page-" + p).classList.contains("active")) return p;
  }
  return null;
}

async function downloadPdf(type) {
  const currentPage = getCurrentPage();
  if (currentPage) readFieldsFromPage(currentPage);

  try {
    const res = await fetch(API_BASE + "/api/generate/" + type, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formData),
    });

    if (!res.ok) {
      const err = await res.json();
      alert("Error: " + (err.error || "Unknown error"));
      return;
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = type.charAt(0).toUpperCase() + type.slice(1) + "_Statement.pdf";
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert("Failed to download. Is the backend running?\n" + err.message);
  }
}

function downloadAll() {
  downloadPdf("expenditure");
  setTimeout(() => downloadPdf("report"), 500);
  setTimeout(() => downloadPdf("schedule"), 1000);
}
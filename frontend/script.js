const introScreen = document.getElementById("introScreen");
const appShell = document.querySelector(".app-shell");
const skipIntroButton = document.getElementById("skipIntro");
const reducedMotionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
let introTimer;

function dismissIntro() {
  if (!introScreen || introScreen.hidden || introScreen.classList.contains("is-exiting")) {
    return;
  }

  window.clearTimeout(introTimer);
  introScreen.classList.add("is-exiting");
  if (appShell) {
    appShell.inert = false;
  }
  window.setTimeout(() => {
    introScreen.hidden = true;
    introScreen.classList.remove("is-visible", "is-exiting");
  }, reducedMotionPreference.matches ? 20 : 650);
}

if (introScreen) {
  introScreen.hidden = false;
  introScreen.classList.add("is-visible");
  if (appShell) {
    appShell.inert = true;
  }
  introTimer = window.setTimeout(dismissIntro, reducedMotionPreference.matches ? 900 : 3000);
  skipIntroButton?.addEventListener("click", dismissIntro);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      dismissIntro();
    }
  });
}

const themeKey = "multi-agent-theme";
const chatHistoryKey = "multi-agent-conversation";
const savedConversationsKey = "multi-agent-saved-conversations";
const activeConversationKey = "multi-agent-active-conversation-id";

const chatContainer = document.getElementById("chatContainer");
const welcomePanel = document.getElementById("welcomePanel");
const chatForm = document.getElementById("chatForm");
const userInput = document.getElementById("userInput");
const typingIndicator = document.getElementById("typingIndicator");
const newChatBtn = document.getElementById("newChatBtn");
const clearChatBtn = document.getElementById("clearChatBtn");
const mobileMenuBtn = document.getElementById("mobileMenuBtn");
const sidebar = document.getElementById("sidebar");
const themeButtons = document.querySelectorAll(".theme-button, .theme-toggle");
const quickActionButtons = document.querySelectorAll(".quick-action");
const suggestionButtons = document.querySelectorAll(".suggestion-card");
const historyBtn = document.getElementById("sidebarHistoryBtn");
const historyPanel = document.getElementById("historyPanel");
const closeHistoryBtn = document.getElementById("closeHistoryBtn");
const historySearchInput = document.getElementById("historySearchInput");
const historyList = document.getElementById("historyList");
const voiceBtn = document.getElementById("sidebarVoiceBtn");
const uploadImageBtn = document.getElementById("uploadImageBtn");
const uploadDocumentBtn = document.getElementById("uploadDocumentBtn");
const imageFileInput = document.getElementById("imageFileInput");
const documentFileInput = document.getElementById("documentFileInput");
const pendingFilesPanel = document.getElementById("pendingFiles");

let conversation = [];
let savedConversations = [];
let activeConversationId = null;
let isGenerating = false;
let voiceRecognition = null;
let pendingAttachments = [];

const MAX_ATTACHMENTS = 5;
const MAX_FILE_SIZE = 10 * 1024 * 1024;
const MAX_TOTAL_FILE_SIZE = 20 * 1024 * 1024;
const IMAGE_MIME_TYPES = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp"
};
const DOCUMENT_MIME_TYPES = {
  ".pdf": "application/pdf",
  ".txt": "text/plain",
  ".md": "text/markdown"
};

function applyTheme(theme) {
  document.body.dataset.theme = theme;
  localStorage.setItem(themeKey, theme);

  themeButtons.forEach((button) => {
    const icon = button.querySelector(".theme-icon") || button;
    button.innerHTML = theme === "dark" ? "☀️" : "🌙";
    if (icon && icon.classList && icon.classList.contains("theme-icon")) {
      button.innerHTML = `<span class="theme-icon">${theme === "dark" ? "☀️" : "🌙"}</span>`;
    }
  });
}

function restoreTheme() {
  const savedTheme = localStorage.getItem(themeKey) || "light";
  applyTheme(savedTheme);
}

function saveConversation() {
  localStorage.setItem(chatHistoryKey, JSON.stringify(conversation));
  localStorage.setItem(activeConversationKey, activeConversationId);

  if (conversation.length === 0) {
    return;
  }

  const firstPrompt = conversation.find((entry) => entry.role === "user")?.content || "New conversation";
  const record = {
    id: activeConversationId,
    title: String(firstPrompt).trim().slice(0, 80),
    updatedAt: Date.now(),
    messages: conversation
  };
  const existingIndex = savedConversations.findIndex((entry) => entry.id === activeConversationId);
  if (existingIndex >= 0) {
    savedConversations[existingIndex] = record;
  } else {
    savedConversations.push(record);
  }
  savedConversations.sort((left, right) => right.updatedAt - left.updatedAt);
  localStorage.setItem(savedConversationsKey, JSON.stringify(savedConversations));
}

function loadConversation() {
  let previousConversation = [];
  try {
    const saved = JSON.parse(localStorage.getItem(chatHistoryKey) || "[]");
    if (Array.isArray(saved)) {
      previousConversation = saved;
    }
  } catch (error) {
    previousConversation = [];
  }

  try {
    const parsedRecords = JSON.parse(localStorage.getItem(savedConversationsKey) || "[]");
    savedConversations = Array.isArray(parsedRecords)
      ? parsedRecords.filter((entry) => entry && typeof entry.id === "string" && Array.isArray(entry.messages))
      : [];
  } catch (error) {
    savedConversations = [];
  }

  const previousConversationId = localStorage.getItem(activeConversationKey);
  const previousRecord = savedConversations.find((entry) => entry.id === previousConversationId);
  if (previousRecord) {
    previousConversation = previousRecord.messages;
  }

  if (previousConversation.length > 0 && !savedConversations.some((entry) => entry.id === previousConversationId)) {
    const firstPrompt = previousConversation.find((entry) => entry.role === "user")?.content || "New conversation";
    savedConversations.push({
      id: previousConversationId || createConversationId(),
      title: String(firstPrompt).trim().slice(0, 80),
      updatedAt: Date.now(),
      messages: previousConversation
    });
    savedConversations.sort((left, right) => right.updatedAt - left.updatedAt);
    localStorage.setItem(savedConversationsKey, JSON.stringify(savedConversations));
  }

  conversation = [];
  activeConversationId = createConversationId();
  localStorage.setItem(chatHistoryKey, JSON.stringify(conversation));
  localStorage.setItem(activeConversationKey, activeConversationId);
}

function createConversationId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function scrollToBottom() {
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function updateWelcomeVisibility() {
  const isEmpty = conversation.length === 0;
  welcomePanel.classList.toggle("hidden", !isEmpty);
  if (isEmpty) {
    welcomePanel.scrollTop = 0;
  }
}

function sanitizeAssistantText(rawText) {
  if (rawText === null || rawText === undefined) {
    return "";
  }

  return String(rawText).replace(/\r\n?/g, "\n").trim();
}

function appendInlineMarkdown(parent, text) {
  const tokenPattern = /(\*\*[^*]+?\*\*|\*[^*]+?\*|`[^`]+`)/g;
  let cursor = 0;

  for (const match of text.matchAll(tokenPattern)) {
    const token = match[0];
    const start = match.index;
    parent.appendChild(document.createTextNode(text.slice(cursor, start)));

    let element;
    let content;
    if (token.startsWith("**")) {
      element = document.createElement("strong");
      content = token.slice(2, -2);
    } else if (token.startsWith("*")) {
      element = document.createElement("em");
      content = token.slice(1, -1);
    } else {
      element = document.createElement("code");
      content = token.slice(1, -1);
    }

    element.textContent = content;
    parent.appendChild(element);
    cursor = start + token.length;
  }

  parent.appendChild(document.createTextNode(text.slice(cursor)));
}

function renderMarkdown(container, markdown) {
  const lines = markdown.replace(/\r/g, "").split("\n");
  let paragraphLines = [];
  let list = null;
  let listType = null;

  function flushParagraph() {
    if (paragraphLines.length === 0) {
      return;
    }

    const paragraph = document.createElement("p");
    paragraphLines.forEach((line, index) => {
      if (index > 0) {
        paragraph.appendChild(document.createElement("br"));
      }
      appendInlineMarkdown(paragraph, line);
    });
    container.appendChild(paragraph);
    paragraphLines = [];
  }

  function flushList() {
    list = null;
    listType = null;
  }

  function appendCodeBlock(codeLines, language) {
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    if (language) {
      code.dataset.language = language;
    }
    code.textContent = codeLines.join("\n");
    pre.appendChild(code);
    container.appendChild(pre);
  }

  let codeLines = null;
  let codeLanguage = "";
  for (const line of lines) {
    if (codeLines) {
      if (/^\s*```\s*$/.test(line)) {
        appendCodeBlock(codeLines, codeLanguage);
        codeLines = null;
        codeLanguage = "";
      } else {
        codeLines.push(line);
      }
      continue;
    }

    const codeFence = line.match(/^\s*```([\w+#.-]*)\s*$/);
    if (codeFence) {
      flushParagraph();
      flushList();
      codeLines = [];
      codeLanguage = codeFence[1];
      continue;
    }

    const headingMatch = line.match(/^\s{0,3}#{1,6}\s+(.+)$/);
    const unorderedMatch = line.match(/^\s*[*+-]\s+(.+)$/);
    const orderedMatch = line.match(/^\s*\d+[.)]\s+(.+)$/);

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    if (/^\s*(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/.test(line)) {
      flushParagraph();
      flushList();
      container.appendChild(document.createElement("hr"));
      continue;
    }

    if (headingMatch) {
      flushParagraph();
      flushList();
      const heading = document.createElement("h3");
      appendInlineMarkdown(heading, headingMatch[1]);
      container.appendChild(heading);
      continue;
    }

    const listMatch = unorderedMatch || orderedMatch;
    if (listMatch) {
      flushParagraph();
      const nextListType = orderedMatch ? "ol" : "ul";
      if (list && listType !== nextListType) {
        flushList();
      }
      if (!list) {
        list = document.createElement(nextListType);
        list.className = "message-list";
        container.appendChild(list);
        listType = nextListType;
      }
      const item = document.createElement("li");
      appendInlineMarkdown(item, listMatch[1]);
      list.appendChild(item);
      continue;
    }

    flushList();
    paragraphLines.push(line);
  }

  if (codeLines) {
    appendCodeBlock(codeLines, codeLanguage);
  }
  flushParagraph();
}

function renderConversation() {
  if (window.MathJax?.typesetClear) {
    window.MathJax.typesetClear([chatContainer]);
  }
  chatContainer.innerHTML = "";

  conversation.forEach((item) => {
    const row = document.createElement("div");
    row.className = `message-row ${item.role === "user" ? "user" : "assistant"}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = item.role === "user" ? "U" : "M";

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";

    if (item.role === "assistant" && item.agent) {
      const agentLabel = document.createElement("div");
      agentLabel.className = "agent-label";
      agentLabel.textContent = item.agent;
      bubble.appendChild(agentLabel);
    }

    const text = document.createElement("div");
    text.className = "message-text";
    if (item.role === "assistant") {
      const sanitizedMessage = sanitizeAssistantText(item.content);
      renderMarkdown(text, sanitizedMessage);
    } else {
      text.textContent = item.content;
    }
    bubble.appendChild(text);

    if (item.role === "user" && Array.isArray(item.attachments) && item.attachments.length > 0) {
      const attachmentList = document.createElement("div");
      attachmentList.className = "message-attachments";
      item.attachments.forEach((attachment) => {
        const badge = document.createElement("span");
        badge.className = "message-attachment";
        const isImage = String(attachment.type || "").startsWith("image/");
        badge.textContent = `${isImage ? "🖼️" : "📄"} ${attachment.name}`;
        attachmentList.appendChild(badge);
      });
      bubble.insertBefore(attachmentList, text);
    }

    if (item.role === "assistant") {
      const actionWrap = document.createElement("div");
      actionWrap.className = "message-actions";

      const copyButton = document.createElement("button");
      copyButton.type = "button";
      copyButton.className = "copy-btn";
      copyButton.textContent = "Copy";
      copyButton.setAttribute("aria-label", "Copy AI response");

      copyButton.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(item.content);
          copyButton.textContent = "Copied ✓";
          setTimeout(() => {
            copyButton.textContent = "Copy";
          }, 1200);
        } catch (error) {
          copyButton.textContent = "Copy failed";
          setTimeout(() => {
            copyButton.textContent = "Copy";
          }, 1200);
        }
      });

      actionWrap.appendChild(copyButton);
      bubble.appendChild(actionWrap);
    }

    row.appendChild(avatar);
    row.appendChild(bubble);
    chatContainer.appendChild(row);
  });

  updateWelcomeVisibility();
  renderHistoryPanel();
  scrollToBottom();
  typesetConversationMath();
}

function typesetConversationMath() {
  const mathJax = window.MathJax;
  if (!mathJax?.startup?.promise || typeof mathJax.typesetPromise !== "function") {
    return;
  }

  mathJax.startup.promise
    .then(() => mathJax.typesetPromise([chatContainer]))
    .catch((error) => {
      console.error("Could not render mathematical notation.", error);
    });
}

function renderPendingFiles() {
  pendingFilesPanel.innerHTML = "";
  pendingFilesPanel.classList.toggle("hidden", pendingAttachments.length === 0);

  pendingAttachments.forEach((file, index) => {
    const chip = document.createElement("div");
    chip.className = "pending-file";

    const name = document.createElement("span");
    name.className = "pending-file-name";
    name.textContent = file.name;

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "remove-file-button";
    removeButton.setAttribute("aria-label", `Remove ${file.name}`);
    removeButton.textContent = "×";
    removeButton.addEventListener("click", () => {
      pendingAttachments.splice(index, 1);
      renderPendingFiles();
    });

    chip.append(name, removeButton);
    pendingFilesPanel.appendChild(chip);
  });
}

function getSupportedFileMimeType(file) {
  const extension = `.${file.name.split(".").pop().toLowerCase()}`;
  return IMAGE_MIME_TYPES[extension] || DOCUMENT_MIME_TYPES[extension] || null;
}

function addSelectedFiles(fileList, expectedKind) {
  const addedFiles = [];
  const rejectedFiles = [];
  for (const file of fileList) {
    const mimeType = getSupportedFileMimeType(file);
    const isExpectedKind = expectedKind === "image"
      ? Boolean(mimeType && mimeType.startsWith("image/"))
      : Boolean(mimeType && !mimeType.startsWith("image/"));
    if (!isExpectedKind || !mimeType || file.size === 0 || file.size > MAX_FILE_SIZE) {
      rejectedFiles.push(file.name);
      continue;
    }
    if (pendingAttachments.length + addedFiles.length >= MAX_ATTACHMENTS) {
      rejectedFiles.push(file.name);
      continue;
    }
    const totalSize = [...pendingAttachments, ...addedFiles].reduce((sum, current) => sum + current.size, 0);
    if (totalSize + file.size > MAX_TOTAL_FILE_SIZE) {
      rejectedFiles.push(file.name);
      continue;
    }
    addedFiles.push(file);
  }

  pendingAttachments.push(...addedFiles);
  renderPendingFiles();
  if (rejectedFiles.length > 0) {
    alert(`Could not add: ${rejectedFiles.join(", ")}. Choose supported files under 10 MB each, with up to 5 files and 20 MB total.`);
  }
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const separator = result.indexOf(",");
      if (separator < 0) {
        reject(new Error(`Could not read ${file.name}. Please choose the file again.`));
        return;
      }
      resolve({
        name: file.name,
        mimeType: getSupportedFileMimeType(file),
        data: result.slice(separator + 1)
      });
    };
    reader.onerror = () => reject(new Error(`Could not read ${file.name}. Please choose the file again.`));
    reader.readAsDataURL(file);
  });
}

function autoResizeTextarea() {
  userInput.style.height = "auto";
  userInput.style.height = `${Math.min(userInput.scrollHeight, 160)}px`;
}

function renderHistoryPanel() {
  const query = (historySearchInput.value || "").trim().toLowerCase();
  const items = savedConversations.filter((record) => {
    const searchableText = `${record.title} ${record.messages.map((entry) => entry.content).join(" ")}`;
    return searchableText.toLowerCase().includes(query);
  });

  historyList.innerHTML = "";

  if (items.length === 0) {
    const empty = document.createElement("li");
    empty.className = "history-empty";
    empty.textContent = "No saved history found.";
    historyList.appendChild(empty);
    return;
  }

  items.forEach((record) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.disabled = isGenerating;
    button.textContent = record.title || "New conversation";
    const date = document.createElement("small");
    date.className = "history-date";
    date.textContent = new Date(record.updatedAt).toLocaleString();
    button.appendChild(date);
    button.addEventListener("click", () => {
      activeConversationId = record.id;
      conversation = record.messages.map((entry) => ({ ...entry }));
      saveConversation();
      renderConversation();
      historyPanel.classList.add("hidden");
    });
    item.appendChild(button);
    historyList.appendChild(item);
  });
}

function toggleHistoryPanel() {
  historyPanel.classList.toggle("hidden");
  if (!historyPanel.classList.contains("hidden")) {
    renderHistoryPanel();
    requestAnimationFrame(() => {
      const sidebarRect = sidebar.getBoundingClientRect();
      const historyRect = historyPanel.getBoundingClientRect();
      sidebar.scrollTo({
        top: sidebar.scrollTop + historyRect.top - sidebarRect.top - 12,
        behavior: "smooth"
      });
      historySearchInput.focus({ preventScroll: true });
    });
  }
}

function startVoiceInput() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    alert("Voice input is not supported in this browser.");
    return;
  }

  if (voiceRecognition) {
    voiceRecognition.stop();
    voiceRecognition = null;
    voiceBtn.classList.remove("listening");
    voiceBtn.textContent = "🎙️ Voice";
    return;
  }

  voiceRecognition = new SpeechRecognition();
  voiceRecognition.lang = "en-US";
  voiceRecognition.interimResults = false;
  voiceRecognition.continuous = false;

  voiceRecognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    const existing = userInput.value.trim();
    userInput.value = existing ? `${existing} ${transcript}` : transcript;
    autoResizeTextarea();
    userInput.focus();
  };

  voiceRecognition.onerror = () => {
    voiceBtn.classList.remove("listening");
    voiceBtn.textContent = "🎙️ Voice";
  };

  voiceRecognition.onend = () => {
    voiceRecognition = null;
    voiceBtn.classList.remove("listening");
    voiceBtn.textContent = "🎙️ Voice";
  };

  voiceBtn.classList.add("listening");
  voiceBtn.textContent = "🎙️ Listening...";
  voiceRecognition.start();
}

function getCompletedConversationHistory(entries) {
  const completedTurns = [];
  let pendingUserMessage = null;

  for (const entry of entries) {
    if (entry.role === "user") {
      pendingUserMessage = entry;
      continue;
    }

    if (entry.role !== "assistant") {
      continue;
    }

    if (entry.agent === "Request status") {
      pendingUserMessage = null;
      continue;
    }

    if (pendingUserMessage) {
      completedTurns.push(
        { role: "user", content: String(pendingUserMessage.content).slice(0, 4000) },
        { role: "assistant", content: String(entry.content).slice(0, 4000) }
      );
      pendingUserMessage = null;
    }
  }

  return completedTurns.slice(-20);
}

async function handleSendPrompt(rawPrompt) {
  if (isGenerating) {
    return;
  }

  const selectedFiles = pendingAttachments.slice();
  const prompt = String(rawPrompt || "").trim()
    || (selectedFiles.length ? "Please analyze the attached file(s)." : "");
  if (!prompt) {
    return;
  }

  isGenerating = true;
  const sendButton = document.querySelector(".send-button");
  if (sendButton) {
    sendButton.disabled = true;
  }
  uploadImageBtn.disabled = true;
  uploadDocumentBtn.disabled = true;
  historyBtn.disabled = true;
  newChatBtn.disabled = true;
  clearChatBtn.disabled = true;
  userInput.disabled = true;

  let requestController;
  let requestTimeout;
  try {
    const previousHistory = getCompletedConversationHistory(conversation);
    conversation.push({
      role: "user",
      content: prompt,
      attachments: selectedFiles.map((file) => ({
        name: file.name,
        type: getSupportedFileMimeType(file)
      }))
    });
    saveConversation();
    renderConversation();

    const requestAttachments = await Promise.all(selectedFiles.map(readFileAsBase64));
    requestController = new AbortController();
    requestTimeout = window.setTimeout(() => requestController.abort(), 40_000);
    typingIndicator.classList.remove("hidden");

    const response = await fetch("/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        message: prompt,
        history: previousHistory,
        attachments: requestAttachments
      }),
      signal: requestController.signal
    });

    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(
        response.ok
          ? "The chatbot server returned an invalid response. Please try again."
          : `The chatbot server returned an unexpected error (HTTP ${response.status}). Please try again.`
      );
    }
    if (!response.ok) {
      const message = typeof data.error === "string"
        ? data.error
        : "The request could not be completed. Please try again.";
      throw new Error(message);
    }

    if (typeof data.reply !== "string" || !data.reply.trim()) {
      throw new Error("No response was received from the AI. Please try again.");
    }

    conversation.push({
      role: "assistant",
      content: sanitizeAssistantText(data.reply),
      agent: typeof data.agent === "string" ? data.agent : "Assistant"
    });
    pendingAttachments = [];
    renderPendingFiles();
    userInput.value = "";
    autoResizeTextarea();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      if (!userInput.value.trim()) {
        userInput.value = prompt;
      }
      autoResizeTextarea();
      conversation.push({
        role: "assistant",
        content: "The request took too long. Your message is still in the input so you can retry.",
        agent: "Request status"
      });
    } else {
      if (!userInput.value.trim()) {
        userInput.value = prompt;
      }
      autoResizeTextarea();
      const message = error instanceof TypeError
        ? "I couldn't reach the chatbot server. Check that Flask is running, then try again."
        : (error instanceof Error ? error.message : "Something went wrong. Please try again.");
      conversation.push({
        role: "assistant",
        content: `Sorry — ${message}`,
        agent: "Request status"
      });
    }
  } finally {
    window.clearTimeout(requestTimeout);
    typingIndicator.classList.add("hidden");
    isGenerating = false;
    if (sendButton) {
      sendButton.disabled = false;
    }
    uploadImageBtn.disabled = false;
    uploadDocumentBtn.disabled = false;
    newChatBtn.disabled = false;
    clearChatBtn.disabled = false;
    historyBtn.disabled = false;
    userInput.disabled = false;
    saveConversation();
    renderConversation();
  }
}

function resetConversation() {
  conversation = [];
  pendingAttachments = [];
  imageFileInput.value = "";
  documentFileInput.value = "";
  renderPendingFiles();
  activeConversationId = createConversationId();
  saveConversation();
  renderConversation();
}

function toggleSidebar() {
  document.body.classList.toggle("sidebar-open");
}

quickActionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    handleSendPrompt(button.dataset.prompt);
    document.body.classList.remove("sidebar-open");
  });
});

suggestionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    handleSendPrompt(button.dataset.prompt);
  });
});

newChatBtn.addEventListener("click", () => {
  resetConversation();
  document.body.classList.remove("sidebar-open");
});

clearChatBtn.addEventListener("click", () => {
  if (conversation.length === 0 || !window.confirm("Clear this conversation and remove it from saved history?")) {
    return;
  }
  savedConversations = savedConversations.filter((record) => record.id !== activeConversationId);
  localStorage.setItem(savedConversationsKey, JSON.stringify(savedConversations));
  resetConversation();
  document.body.classList.remove("sidebar-open");
});

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  handleSendPrompt(userInput.value);
});

userInput.addEventListener("input", autoResizeTextarea);

userInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    handleSendPrompt(userInput.value);
  }
});

mobileMenuBtn.addEventListener("click", () => {
  toggleSidebar();
});

themeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const currentTheme = document.body.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(currentTheme);
  });
});

historyBtn.addEventListener("click", toggleHistoryPanel);
closeHistoryBtn.addEventListener("click", () => historyPanel.classList.add("hidden"));
historySearchInput.addEventListener("input", renderHistoryPanel);
voiceBtn.addEventListener("click", startVoiceInput);
document.addEventListener("mathjax-ready", typesetConversationMath);
uploadImageBtn.addEventListener("click", () => imageFileInput.click());
uploadDocumentBtn.addEventListener("click", () => documentFileInput.click());
imageFileInput.addEventListener("change", () => {
  addSelectedFiles(imageFileInput.files, "image");
  imageFileInput.value = "";
});
documentFileInput.addEventListener("change", () => {
  addSelectedFiles(documentFileInput.files, "document");
  documentFileInput.value = "";
});

document.addEventListener("click", (event) => {
  const clickedInsideSidebar = sidebar.contains(event.target);
  const clickedMenuButton = mobileMenuBtn.contains(event.target);
  const clickedHistoryPanel = historyPanel.contains(event.target);
  const clickedHistoryButton = historyBtn.contains(event.target);

  if (window.innerWidth <= 860 && !clickedInsideSidebar && !clickedMenuButton) {
    document.body.classList.remove("sidebar-open");
  }

  if (!clickedHistoryPanel && !clickedHistoryButton && !historyPanel.classList.contains("hidden")) {
    historyPanel.classList.add("hidden");
  }
});

window.addEventListener("resize", () => {
  if (window.innerWidth > 860) {
    document.body.classList.remove("sidebar-open");
  }
});

loadConversation();
restoreTheme();
renderConversation();
autoResizeTextarea();

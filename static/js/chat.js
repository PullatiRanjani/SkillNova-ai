document.addEventListener("DOMContentLoaded", () => {

    const messagesBox =
        document.querySelector(".messages");

    const textarea =
        document.querySelector(".composer textarea");

    const sendButton =
        document.querySelector(".composer button");

    const suggestionButtons =
        document.querySelectorAll(".suggestions button");


    // =========================================================
    // MARKDOWN → SAFE HTML
    // =========================================================

    function formatMessage(text) {

        if (!text) return "";

        // First escape HTML
        let html = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");

        // Code blocks
        html = html.replace(
            /```([\s\S]*?)```/g,
            "<pre><code>$1</code></pre>"
        );

        // Inline code
        html = html.replace(
            /`([^`]+)`/g,
            "<code>$1</code>"
        );

        // Bold
        html = html.replace(
            /\*\*(.*?)\*\*/g,
            "<strong>$1</strong>"
        );

        // Italic
        html = html.replace(
            /(?<!\*)\*([^*\n]+)\*(?!\*)/g,
            "<em>$1</em>"
        );

        // Bullet points
        html = html.replace(
            /^[•\-]\s+(.*)$/gm,
            "<div class=\"chat-bullet\">• $1</div>"
        );

        // Numbered lists
        html = html.replace(
            /^(\d+)\.\s+(.*)$/gm,
            "<div class=\"chat-number\">$1. $2</div>"
        );

        // New lines
        html = html.replace(
            /\n/g,
            "<br>"
        );

        return html;
    }


    // =========================================================
    // ADD MESSAGE
    // =========================================================

    function addMessage(
        text,
        type
    ) {

        if (!messagesBox) return;

        const message =
            document.createElement("div");

        message.className =
            `msg ${type}`;

        const bubble =
            document.createElement("div");

        bubble.className =
            "bubble";

        if (type === "assistant") {

            bubble.innerHTML =
                formatMessage(text);

        } else {

            // User message should remain plain text
            bubble.textContent =
                text;
        }

        message.appendChild(
            bubble
        );

        messagesBox.appendChild(
            message
        );

        messagesBox.scrollTop =
            messagesBox.scrollHeight;
    }


    // =========================================================
    // TYPING INDICATOR
    // =========================================================

    function showTyping() {

        if (!messagesBox) return null;

        const message =
            document.createElement("div");

        message.className =
            "msg assistant typing-message";

        const bubble =
            document.createElement("div");

        bubble.className =
            "bubble typing";

        bubble.innerHTML =
            "<span></span><span></span><span></span>";

        message.appendChild(
            bubble
        );

        messagesBox.appendChild(
            message
        );

        messagesBox.scrollTop =
            messagesBox.scrollHeight;

        return message;
    }


    // =========================================================
    // SEND MESSAGE
    // =========================================================

    async function sendMessage(text = null) {

        if (!textarea) return;

        const message =
            text !== null
                ? text.trim()
                : textarea.value.trim();

        if (!message) return;

        if (text === null) {
            textarea.value = "";
        }

        // Show user message
        addMessage(
            message,
            "user"
        );

        // Disable while sending
        if (sendButton) {
            sendButton.disabled = true;
        }

        textarea.disabled = true;

        const typing =
            showTyping();

        try {

            // Get context
            const roleInput =
                document.querySelector(
                    'input[name="role"]'
                );

            const courseInput =
                document.querySelector(
                    'input[name="course"]'
                );

            const role =
                roleInput
                    ? roleInput.value.trim()
                    : "";

            const course =
                courseInput
                    ? courseInput.value.trim()
                    : "";


            const response =
                await fetch(
                    "/api/chat",
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body: JSON.stringify({
                            message: message,
                            role: role,
                            course: course
                        })
                    }
                );


            const data =
                await response.json();


            // Remove typing
            if (typing) {
                typing.remove();
            }


            if (!response.ok) {

                addMessage(
                    data.error ||
                    "Something went wrong. Please try again.",
                    "assistant"
                );

                return;
            }


            // Assistant reply
            addMessage(
                data.reply ||
                "I couldn't generate a response.",
                "assistant"
            );


        } catch (error) {

            console.error(
                "Chat error:",
                error
            );

            if (typing) {
                typing.remove();
            }

            addMessage(
                "Unable to connect to the AI service. Please try again.",
                "assistant"
            );

        } finally {

            if (sendButton) {
                sendButton.disabled = false;
            }

            textarea.disabled = false;

            textarea.focus();
        }
    }


    // =========================================================
    // SEND BUTTON
    // =========================================================

    if (sendButton) {

        sendButton.addEventListener(
            "click",
            () => {
                sendMessage();
            }
        );
    }


    // =========================================================
    // ENTER KEY
    // =========================================================

    if (textarea) {

        textarea.addEventListener(
            "keydown",
            (event) => {

                if (
                    event.key === "Enter" &&
                    !event.shiftKey
                ) {

                    event.preventDefault();

                    sendMessage();
                }
            }
        );
    }


    // =========================================================
    // SUGGESTION BUTTONS
    // =========================================================

    suggestionButtons.forEach(
        button => {

            button.addEventListener(
                "click",
                () => {

                    const text =
                        button.textContent.trim();

                    if (text) {
                        sendMessage(text);
                    }
                }
            );
        }
    );


    // =========================================================
    // AUTO SCROLL
    // =========================================================

    if (messagesBox) {

        messagesBox.scrollTop =
            messagesBox.scrollHeight;
    }

});
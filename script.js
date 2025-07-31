document.addEventListener('DOMContentLoaded', () => {
    const messagesWrapper = document.getElementById('messagesWrapper');
    const messagesDisplay = document.getElementById('messagesDisplay');
    const userInput = document.getElementById('userInput');
    const sendMessageBtn = document.getElementById('sendMessageBtn');
    const newChatBtn = document.getElementById('newChatBtn');
    const newChatIconBtn = document.getElementById('newChatIconBtn');
    const clearHistoryBtn = document.getElementById('clearHistoryBtn');
    const sidebarNav = document.querySelector('.sidebar-nav');
    const sidebar = document.getElementById('sidebar');
    const chatInputArea = document.getElementById('chatInputArea');
    const sidebarToggleBtn = document.getElementById('sidebarToggleBtn');

    let currentSessionId = localStorage.getItem('currentSessionId');
    let currentSessionLocked = false;
    const initialGreetingMessage = "Hello! I am Grip. How can I help you today?";

    let isTyping = false;
    let animationFrameId;

    sidebarToggleBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        sidebar.classList.toggle('collapsed');
    });

    const handleResponsiveLayout = () => {
        if (window.innerWidth <= 768) {
            sidebar.classList.add('collapsed');
        } else {
            sidebar.classList.remove('collapsed');
        }
    };
    window.addEventListener('resize', handleResponsiveLayout);

    function setInitialGreeting() {
        messagesDisplay.innerHTML = `<div class="initial-greeting">${initialGreetingMessage}</div>`;
    }

    function appendLockedSessionNotice() {
        const lockedNotice = document.createElement('div');
        lockedNotice.className = 'locked-session-notice';
        lockedNotice.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect width="18" height="11" x="3" y="11" rx="2" ry="2"/>
                <circle cx="12" cy="16" r="1"/>
                <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
            </svg>
            <span>
                This chat is <strong>locked</strong> and cannot accept new messages, but you can review previous messages above.
            </span>
        `;
        messagesDisplay.appendChild(lockedNotice);
        messagesWrapper.scrollTop = messagesWrapper.scrollHeight;
    }

    function updateInputState(locked = false) {
        currentSessionLocked = locked;
        if (locked) {
            userInput.disabled = true;
            sendMessageBtn.disabled = true;
            chatInputArea.classList.add('disabled');
            userInput.placeholder = "Session locked - start a new chat";
        } else {
            userInput.disabled = false;
            sendMessageBtn.disabled = false;
            chatInputArea.classList.remove('disabled');
            userInput.placeholder = "Send a message...";
        }
    }

    // --- Session Management ---
    async function loadSessions() {
        try {
            const response = await fetch('http://127.0.0.1:5001/get_sessions');
            const sessions = await response.json();
            sidebarNav.innerHTML = '';

            if (sessions.length === 0) {
                localStorage.removeItem('currentSessionId');
                currentSessionId = null;
            }

            sessions.forEach(session => {
                const sessionItem = document.createElement('div');
                sessionItem.classList.add('session-item');

                const sessionLink = document.createElement('a');
                sessionLink.href = '#';
                sessionLink.textContent = session.preview;
                sessionLink.dataset.sessionId = session.id;
                sessionLink.classList.add('session-link');

                if (session.is_locked) {
                    sessionLink.classList.add('locked');
                }

                sessionLink.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (isTyping) return;
                    switchSession(session.id);
                    if (window.innerWidth <= 768) {
                        sidebar.classList.add('collapsed');
                    }
                });

                const deleteBtn = document.createElement('button');
                deleteBtn.classList.add('delete-session-btn');
                deleteBtn.title = 'Delete session';
                deleteBtn.innerHTML = `
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M18 6 6 18"/>
                        <path d="m6 6 12 12"/>
                    </svg>
                `;

                deleteBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    if (isTyping) return;
                    deleteSession(session.id);
                });

                sessionItem.appendChild(sessionLink);
                sessionItem.appendChild(deleteBtn);
                sidebarNav.appendChild(sessionItem);
            });

            updateActiveSessionLink();
        } catch (error) {
            console.error('Failed to load sessions:', error);
        }
    }

    async function deleteSession(sessionId) {
        if (!confirm('Are you sure you want to delete this session?')) {
            return;
        }
        try {
            const response = await fetch(`http://127.0.0.1:5001/delete_session/${sessionId}`, {
                method: 'DELETE'
            });
            if (response.ok) {
                if (sessionId === currentSessionId) {
                    messagesDisplay.innerHTML = '';
                    setInitialGreeting();
                    userInput.value = '';
                    userInput.style.height = 'auto';
                    updateInputState(false);
                    currentSessionId = null;
                    localStorage.removeItem('currentSessionId');
                    await loadSessions();
                    updateActiveSessionLink();
                } else {
                    await loadSessions();
                }
            } else {
                console.error('Failed to delete session');
            }
        } catch (error) {
            console.error('Error deleting session:', error);
        }
    }

    async function clearAllSessions() {
        if (!confirm('Are you sure you want to clear all chat history? This cannot be undone.')) {
            return;
        }
        try {
            const response = await fetch('http://127.0.0.1:5001/clear_all_sessions', {
                method: 'POST'
            });
            if (response.ok) {
                messagesDisplay.innerHTML = '';
                setInitialGreeting();
                userInput.value = '';
                userInput.style.height = 'auto';
                updateInputState(false);
                currentSessionId = null;
                localStorage.removeItem('currentSessionId');
                await loadSessions();
                updateActiveSessionLink();
            } else {
                console.error('Failed to clear all sessions');
            }
        } catch (error) {
            console.error('Error clearing all sessions:', error);
        }
    }

    async function switchSession(sessionId) {
        if (!sessionId || isTyping) return;
        currentSessionId = sessionId;
        localStorage.setItem('currentSessionId', currentSessionId);
        messagesDisplay.innerHTML = '';

        try {
            const response = await fetch(`http://127.0.0.1:5001/get_session_history/${sessionId}`);
            if (response.status === 404) {
                currentSessionId = null;
                localStorage.removeItem('currentSessionId');
                messagesDisplay.innerHTML = '';
                setInitialGreeting();
                updateActiveSessionLink();
                return;
            }
            if (!response.ok) throw new Error(`Server responded with status: ${response.status}`);

            const data = await response.json();
            const history = data.history;
            const isLocked = data.is_locked;

            updateInputState(isLocked);

            if (history && history.length > 0) {
                history.forEach(message => appendMessage(message.content, message.role === 'user' ? 'user-message' : 'bot-message'));
            } else {
                setInitialGreeting();
            }

            if (isLocked) {
                appendLockedSessionNotice();
            }
            updateActiveSessionLink();
        } catch (error) {
            console.error('Failed to switch session:', error);
            currentSessionId = null;
            localStorage.removeItem('currentSessionId');
            messagesDisplay.innerHTML = '';
            setInitialGreeting();
            updateActiveSessionLink();
        }
        userInput.focus();
    }

    function updateActiveSessionLink() {
        document.querySelectorAll('.session-link').forEach(link => {
            link.classList.toggle('active', link.dataset.sessionId === currentSessionId);
        });
    }

    // --- Message Handling ---
    newChatBtn.addEventListener('click', () => {
        messagesDisplay.innerHTML = '';
        setInitialGreeting();
        userInput.value = '';
        userInput.style.height = 'auto';
        updateInputState(false);
        currentSessionId = null;
        localStorage.removeItem('currentSessionId');
        updateActiveSessionLink();
    });
    newChatIconBtn.addEventListener('click', () => {
        messagesDisplay.innerHTML = '';
        setInitialGreeting();
        userInput.value = '';
        userInput.style.height = 'auto';
        updateInputState(false);
        currentSessionId = null;
        localStorage.removeItem('currentSessionId');
        updateActiveSessionLink();
    });
    clearHistoryBtn.addEventListener('click', clearAllSessions);

    sendMessageBtn.addEventListener('click', handleSendMessage);
    userInput.addEventListener('keypress', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            handleSendMessage();
        }
    });

    async function handleSendMessage() {
        if (isTyping || currentSessionLocked) return;
        const messageText = userInput.value.trim();
        if (messageText === '') return;

        if (!currentSessionId) {
            const response = await fetch('http://127.0.0.1:5001/new_chat', { method: 'POST' });
            const data = await response.json();
            currentSessionId = data.session_id;
            localStorage.setItem('currentSessionId', currentSessionId);
            await loadSessions();
            updateActiveSessionLink();
        }

        isTyping = true;
        userInput.disabled = true;
        sendMessageBtn.disabled = true;

        const isFirstMessage = !messagesDisplay.querySelector('.user-message');
        const initialGreetingElement = messagesDisplay.querySelector('.initial-greeting');
        if (initialGreetingElement) initialGreetingElement.remove();

        appendMessage(messageText, 'user-message');
        userInput.value = '';
        userInput.style.height = 'auto';

        await fetchBotResponse(messageText, isFirstMessage);
    }

    function appendMessage(text, type) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', type);
        const contentContainer = document.createElement('div');
        contentContainer.classList.add('message-content');
        if (type.includes('bot-message')) {
            contentContainer.innerHTML = marked.parse(text, { gfm: true, breaks: true, smartypants: false });
        } else {
            contentContainer.textContent = text;
        }
        messageElement.appendChild(contentContainer);
        messagesDisplay.appendChild(messageElement);
        messagesWrapper.scrollTop = messagesWrapper.scrollHeight;
        return messageElement;
    }

    function typeWriter(element, text) {
        return new Promise((resolve) => {
            let i = 0;
            let lastTime = 0;
            const speed = 5;
            function animate(currentTime) {
                if (!lastTime) lastTime = currentTime;
                const deltaTime = currentTime - lastTime;
                if (deltaTime > speed) {
                    if (i < text.length) {
                        element.innerHTML = marked.parse(text.substring(0, i + 1), { gfm: true, breaks: true, smartypants: false });
                        messagesWrapper.scrollTop = messagesWrapper.scrollHeight;
                        i++;
                        lastTime = currentTime;
                    } else {
                        cancelAnimationFrame(animationFrameId);
                        element.innerHTML = marked.parse(text, { gfm: true, breaks: true, smartypants: false });
                        messagesWrapper.scrollTop = messagesWrapper.scrollHeight;
                        resolve();
                        return;
                    }
                }
                animationFrameId = requestAnimationFrame(animate);
            }
            animationFrameId = requestAnimationFrame(animate);
        });
    }

    async function fetchBotResponse(userMessage, isFirstMessage) {
        const botMessageElement = appendMessage('', 'bot-message');
        const contentContainer = botMessageElement.querySelector('.message-content');
        contentContainer.innerHTML = `<div class="typing-indicator"><span></span><span></span><span></span></div>`;
        messagesWrapper.scrollTop = messagesWrapper.scrollHeight;

        try {
            const response = await fetch('http://127.0.0.1:5001/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: userMessage, session_id: currentSessionId }),
            });
            const data = await response.json();

            if (response.status === 423) {
                updateInputState(true);
                appendLockedSessionNotice();
                botMessageElement.remove();
                await loadSessions();
                return;
            }

            if (response.status === 400) {
                contentContainer.innerHTML = '';
                await typeWriter(contentContainer, data.reply);
                await loadSessions();
            } else if (!response.ok) {
                throw new Error(data.error || 'Network response was not ok');
            } else {
                contentContainer.innerHTML = '';
                await typeWriter(contentContainer, data.reply);

                if (isFirstMessage) await loadSessions();
            }
        } catch (error) {
            console.error('Error fetching bot response:', error);
            botMessageElement.classList.add('error-message');
            contentContainer.textContent = `Error: ${error.message}.`;
        } finally {
            isTyping = false;
            if (!currentSessionLocked) {
                userInput.disabled = false;
                sendMessageBtn.disabled = false;
                userInput.focus();
            }
            messagesWrapper.scrollTop = messagesWrapper.scrollHeight;
        }
    }

    async function initializeApp() {
        await loadSessions();
        if (currentSessionId) {
            await switchSession(currentSessionId);
        } else {
            messagesDisplay.innerHTML = '';
            setInitialGreeting();
        }
        handleResponsiveLayout();
    }

    initializeApp();
});
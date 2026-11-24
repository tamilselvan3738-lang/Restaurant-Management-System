/**
 * ============================================================================
 * CLASSIC & UNIQUE CULINARY CLICK ANIMATION ENGINE & THEME CONTROLLER
 * ============================================================================
 * Features:
 * - 100% Vector Icons (NO Emojis) using crisp Bootstrap Icons & SVGs
 * - 5 Distinct Food-Menu Color Themes (Saffron, Basil, Merlot, Espresso, Citrus)
 * - Classic & Unique Animation Engine:
 *     1. Classic Royal Velvet Ripple with Ambient Aura
 *     2. Gourmet Vector Culinary Icon Bloom (Cloche, Flame, Wine, Roast, Toque)
 *     3. Michelin Golden Starlight & Diamond Sparkles
 *     4. Sizzling Hearth Flame Embers
 *     5. Luxury Geometric Diamond & Crosshair Pulse
 * - Tactile Classic Press Physics on all interactive elements
 * - Synthesized Acoustic Pop (Web Audio API, optional/toggleable)
 * ============================================================================
 */

(function () {
    'use strict';

    // ------------------------------------------------------------------------
    // 1. CULINARY THEMES CONFIGURATION (100% ICON BASED - NO EMOJIS)
    // ------------------------------------------------------------------------
    const CULINARY_THEMES = {
        saffron: {
            name: "Saffron & Searing Flame",
            icon: "bi-fire",
            primary: "#f25c05",
            accent: "#f59e0b",
            description: "Warm spicy ember, charred grill, and golden saffron"
        },
        basil: {
            name: "Fresh Basil & Avocado",
            icon: "bi-flower1",
            primary: "#10b981",
            accent: "#84cc16",
            description: "Farm-to-table garden herbs, crisp basil, and lime zest"
        },
        merlot: {
            name: "Merlot Wine & Velvet Berry",
            icon: "bi-cup-straw",
            primary: "#be185d",
            accent: "#f43f5e",
            description: "Rich burgundy cellar wine and dark blackberry glaze"
        },
        espresso: {
            name: "Espresso Truffle & Caramel",
            icon: "bi-cup-hot-fill",
            primary: "#b45309",
            accent: "#d97706",
            description: "Artisan dark roast, smoked toffee, and cocoa truffle"
        },
        citrus: {
            name: "Citrus Punch & Honey Glaze",
            icon: "bi-sun-fill",
            primary: "#eab308",
            accent: "#f97316",
            description: "Sun-drenched honey glaze, yuzu, and ripe tangerine"
        }
    };

    // Load initial culinary theme and dark/light mode
    const savedTheme = localStorage.getItem('rms-culinary-theme') || 'saffron';
    document.documentElement.setAttribute('data-culinary-theme', savedTheme);

    window.setCulinaryTheme = function (themeKey) {
        if (!CULINARY_THEMES[themeKey]) return;
        document.documentElement.setAttribute('data-culinary-theme', themeKey);
        localStorage.setItem('rms-culinary-theme', themeKey);
        updateThemeUI(themeKey);

        // Trigger a classic celebration ripple in current theme color
        const centerX = window.innerWidth / 2;
        const centerY = window.innerHeight / 3;
        spawnClassicRipple(centerX, centerY);
        spawnGoldenStars(centerX, centerY);
    };

    window.toggleCulinaryMode = function () {
        if (typeof window.toggleTheme === 'function') {
            window.toggleTheme();
        } else {
            const currentMode = document.documentElement.getAttribute('data-bs-theme') || 'light';
            const newMode = currentMode === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-bs-theme', newMode);
            localStorage.setItem('rms-theme', newMode);
            updateModeUI(newMode);
        }
    };

    function updateThemeUI(activeKey) {
        document.querySelectorAll('[data-culinary-select]').forEach(el => {
            const key = el.getAttribute('data-culinary-select');
            if (key === activeKey) {
                el.classList.add('active-theme');
            } else {
                el.classList.remove('active-theme');
            }
        });

        const currentLabel = document.getElementById('currentThemeLabel');
        if (currentLabel && CULINARY_THEMES[activeKey]) {
            currentLabel.innerHTML = `<i class="bi ${CULINARY_THEMES[activeKey].icon} me-1"></i> ${CULINARY_THEMES[activeKey].name}`;
        }
    }

    function updateModeUI(mode) {
        const icon = document.getElementById('themeModeIcon');
        const label = document.getElementById('themeModeLabel');
        if (icon) {
            icon.className = mode === 'dark' ? 'bi bi-sun-fill text-warning' : 'bi bi-moon-stars-fill text-primary';
        }
        if (label) {
            label.textContent = mode === 'dark' ? 'Light Mode' : 'Dark Mode';
        }

        const iconLaptop = document.getElementById('themeIconLaptop');
        const labelLaptop = document.getElementById('themeLabelLaptop');
        if (iconLaptop && labelLaptop) {
            iconLaptop.className = mode === 'dark' ? 'bi bi-sun-fill text-warning' : 'bi bi-moon-stars-fill text-secondary';
            labelLaptop.textContent = mode === 'dark' ? 'Light Mode' : 'Dark Mode';
        }

        const mobileIcon = document.getElementById('themeIconMobile');
        const mobileLabel = document.getElementById('themeLabelMobile');
        const mobileStatus = document.getElementById('mobileThemeStatus');
        if (mobileIcon) {
            mobileIcon.className = mode === 'dark' ? 'bi bi-sun-fill text-warning' : 'bi bi-moon-stars-fill text-light';
        }
        if (mobileLabel) {
            mobileLabel.textContent = mode === 'dark' ? 'Light' : 'Dark';
        }
        if (mobileStatus) {
            mobileStatus.textContent = mode === 'dark' ? 'Dark' : 'Light';
        }
    }
    window.updateModeUI = updateModeUI;

    // ------------------------------------------------------------------------
    // 2. CLASSIC & UNIQUE CULINARY VECTOR CLICK ANIMATIONS (NO EMOJIS)
    // ------------------------------------------------------------------------
    // Refined Bootstrap Vector Icons for gourmet dining
    const CULINARY_VECTOR_ICONS = [
        'bi-fire',              // Searing grill flame
        'bi-bell-fill',         // Classic dining cloche cover
        'bi-cup-hot-fill',      // Artisan roast & brew
        'bi-stars',             // Michelin sparkle
        'bi-award-fill',        // Culinary badge of honor
        'bi-flower1',           // Garden fresh herb
        'bi-gem',               // Luxury quality
        'bi-egg-fried',         // Artisan skillet
        'bi-pie-chart-fill',    // Gourmet slice
        'bi-shield-check',      // Premium certified
        'bi-sparkles'           // Golden glint
    ];

    let clickCounter = 0;
    let clickFxEnabled = true;
    let audioFxEnabled = false;
    let audioCtx = null;

    let fxContainer = null;
    function getFxContainer() {
        if (!fxContainer || !document.body.contains(fxContainer)) {
            fxContainer = document.createElement('div');
            fxContainer.id = 'rms-click-fx-canvas-container';
            document.body.appendChild(fxContainer);
        }
        return fxContainer;
    }

    // Synthesized acoustic click pop (Web Audio API)
    function playCulinaryPop() {
        if (!audioFxEnabled) return;
        try {
            if (!audioCtx) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                audioCtx = new AudioContext();
            }
            if (audioCtx.state === 'suspended') {
                audioCtx.resume();
            }
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = 'sine';
            const now = audioCtx.currentTime;
            osc.frequency.setValueAtTime(480, now);
            osc.frequency.exponentialRampToValueAtTime(960, now + 0.07);
            gain.gain.setValueAtTime(0.06, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.start(now);
            osc.stop(now + 0.09);
        } catch (e) {}
    }

    // ------------------------------------------------------------------------
    // ANIMATION 1: Classic Royal Velvet Ripple with Ambient Aura
    // ------------------------------------------------------------------------
    function spawnClassicRipple(x, y) {
        const container = getFxContainer();
        
        // Outer expanding soft halo
        const aura = document.createElement('div');
        aura.className = 'fx-classic-aura';
        aura.style.left = `${x}px`;
        aura.style.top = `${y}px`;
        container.appendChild(aura);
        setTimeout(() => aura.remove(), 750);

        // Dual precision hairline concentric shockwave rings
        for (let i = 0; i < 2; i++) {
            const ring = document.createElement('div');
            ring.className = 'fx-classic-ripple-ring';
            ring.style.left = `${x}px`;
            ring.style.top = `${y}px`;
            ring.style.animationDelay = `${i * 0.09}s`;
            container.appendChild(ring);
            setTimeout(() => ring.remove(), 700);
        }
    }

    // ------------------------------------------------------------------------
    // ANIMATION 2: Gourmet Vector Culinary Icon Bloom
    // ------------------------------------------------------------------------
    function spawnVectorIconBloom(x, y) {
        const container = getFxContainer();
        const count = 5 + Math.floor(Math.random() * 2); // 5 to 6 vector icons
        for (let i = 0; i < count; i++) {
            const el = document.createElement('div');
            el.className = 'fx-classic-icon-particle';
            
            const iconClass = CULINARY_VECTOR_ICONS[Math.floor(Math.random() * CULINARY_VECTOR_ICONS.length)];
            el.innerHTML = `<i class="bi ${iconClass}"></i>`;

            // Radial trajectories with smooth deceleration
            const angle = (i / count) * Math.PI * 2 + (Math.random() * 0.3);
            const distance = 40 + Math.random() * 65;
            const tx = Math.cos(angle) * distance;
            const ty = Math.sin(angle) * distance - 20; // gentle float upward
            const rot = -30 + Math.random() * 60;

            el.style.left = `${x}px`;
            el.style.top = `${y}px`;
            el.style.setProperty('--fx-tx', `${tx}px`);
            el.style.setProperty('--fx-ty', `${ty}px`);
            el.style.setProperty('--fx-rot', `${rot}deg`);

            container.appendChild(el);
            setTimeout(() => el.remove(), 800);
        }
    }

    // ------------------------------------------------------------------------
    // ANIMATION 3: Michelin Golden Starlight & Diamond Sparkles
    // ------------------------------------------------------------------------
    function spawnGoldenStars(x, y) {
        const container = getFxContainer();
        const count = 5 + Math.floor(Math.random() * 3);
        const starIcons = ['bi-stars', 'bi-star-fill', 'bi-sparkles', 'bi-gem'];

        for (let i = 0; i < count; i++) {
            const el = document.createElement('div');
            el.className = 'fx-luxury-star';
            const iconClass = starIcons[Math.floor(Math.random() * starIcons.length)];
            el.innerHTML = `<i class="bi ${iconClass}"></i>`;

            const angle = (i / count) * Math.PI * 2 + (Math.random() * 0.35);
            const dist = 32 + Math.random() * 55;
            const tx = Math.cos(angle) * dist;
            const ty = Math.sin(angle) * dist;

            el.style.left = `${x}px`;
            el.style.top = `${y}px`;
            el.style.setProperty('--fx-tx', `${tx}px`);
            el.style.setProperty('--fx-ty', `${ty}px`);

            container.appendChild(el);
            setTimeout(() => el.remove(), 750);
        }
    }

    // ------------------------------------------------------------------------
    // ANIMATION 4: Sizzling Hearth Flame Embers
    // ------------------------------------------------------------------------
    function spawnSizzlingEmbers(x, y) {
        const container = getFxContainer();
        const count = 12 + Math.floor(Math.random() * 6);
        for (let i = 0; i < count; i++) {
            const el = document.createElement('div');
            el.className = 'fx-ember-spark';

            const angle = Math.random() * Math.PI * 2;
            const speed = 35 + Math.random() * 80;
            const tx = Math.cos(angle) * speed;
            const ty = Math.sin(angle) * speed - 15;
            const size = 4 + Math.random() * 5;

            el.style.left = `${x}px`;
            el.style.top = `${y}px`;
            el.style.width = `${size}px`;
            el.style.height = `${size}px`;
            el.style.setProperty('--fx-tx', `${tx}px`);
            el.style.setProperty('--fx-ty', `${ty}px`);

            container.appendChild(el);
            setTimeout(() => el.remove(), 750);
        }
    }

    // ------------------------------------------------------------------------
    // ANIMATION 5: Luxury Geometric Diamond Pulse
    // ------------------------------------------------------------------------
    function spawnDiamondPulse(x, y) {
        const container = getFxContainer();

        const pulse = document.createElement('div');
        pulse.className = 'fx-diamond-pulse';
        pulse.innerHTML = `<i class="bi bi-gem"></i>`;
        pulse.style.left = `${x}px`;
        pulse.style.top = `${y}px`;

        container.appendChild(pulse);
        setTimeout(() => pulse.remove(), 750);

        // Pair with micro golden ripples
        const ring = document.createElement('div');
        ring.className = 'fx-classic-ripple-ring';
        ring.style.left = `${x}px`;
        ring.style.top = `${y}px`;
        container.appendChild(ring);
        setTimeout(() => ring.remove(), 700);
    }

    // ------------------------------------------------------------------------
    // TACTILE CLASSIC ELEMENT SQUISH / SPRING PRESS
    // ------------------------------------------------------------------------
    function triggerTactileBounce(target) {
        if (!target) return;
        const interactive = target.closest('button, a, .pos-item-card, .menu-item-card, .cat-pill, .badge, .card, tr, .btn, input[type="submit"]');
        if (interactive) {
            interactive.classList.remove('click-spring-active');
            void interactive.offsetWidth;
            interactive.classList.add('click-spring-active');
            setTimeout(() => {
                interactive.classList.remove('click-spring-active');
            }, 380);
        }
    }

    // ------------------------------------------------------------------------
    // GLOBAL CLICK HANDLER: ROTATES UNIQUE ANIMATIONS ON EVERY CLICK
    // ------------------------------------------------------------------------
    function handleGlobalClick(e) {
        if (!clickFxEnabled) return;

        const x = e.clientX;
        const y = e.clientY;

        if (x === undefined || y === undefined || (x === 0 && y === 0)) return;

        // Tactile spring press feedback
        triggerTactileBounce(e.target);

        // Acoustic pop sound (if enabled)
        playCulinaryPop();

        // Context-aware selection
        const target = e.target;
        const isFoodCard = target.closest('.pos-item-card, .menu-item-card, .food-item');
        const isCheckoutBtn = target.closest('[type="submit"], .btn-success, .btn-primary, [href*="checkout"], [href*="pay"]');

        if (isFoodCard) {
            spawnVectorIconBloom(x, y);
            spawnClassicRipple(x, y);
        } else if (isCheckoutBtn) {
            spawnGoldenStars(x, y);
            spawnClassicRipple(x, y);
        } else {
            // Cycle through 5 unique classic animations on each click
            const effectIndex = clickCounter % 5;
            clickCounter++;

            switch (effectIndex) {
                case 0:
                    spawnClassicRipple(x, y);
                    break;
                case 1:
                    spawnVectorIconBloom(x, y);
                    break;
                case 2:
                    spawnGoldenStars(x, y);
                    break;
                case 3:
                    spawnSizzlingEmbers(x, y);
                    break;
                case 4:
                    spawnDiamondPulse(x, y);
                    break;
            }
        }
    }

    // Listen globally
    window.addEventListener('pointerdown', handleGlobalClick, { passive: true });

    // Expose helpers
    window.toggleClickFx = function () {
        clickFxEnabled = !clickFxEnabled;
    };

    window.toggleAudioFx = function () {
        audioFxEnabled = !audioFxEnabled;
        if (audioFxEnabled) {
            playCulinaryPop();
        }
    };

    // DOM Ready
    document.addEventListener('DOMContentLoaded', () => {
        updateThemeUI(savedTheme);
        const currentMode = document.documentElement.getAttribute('data-bs-theme') || 'light';
        updateModeUI(currentMode);
    });

})();

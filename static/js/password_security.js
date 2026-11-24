/**
 * Interactive Password Security & Live Validation Checklist
 * Dynamically tests password requirements as user types:
 * - Green (text-success) with checkmark icon when satisfied
 * - Red (text-danger) with cross icon when unsatisfied
 * - Real-time password strength meter (Very Weak -> Very Strong)
 * - Password confirmation match feedback
 * - Show/Hide password toggle
 */

const COMMON_PASSWORDS = new Set([
    'password', 'password1', 'password123', 'admin', 'admin123', '123456',
    '12345678', '123456789', 'qwerty', 'letmein', 'welcome', 'welcome123',
    'restaurant', 'manager', 'grandbistro', 'bistro123', 'system123', 'pass1234'
]);

function togglePasswordVisibility(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';
    const icon = btn.querySelector('i');
    if (icon) {
        if (isPassword) {
            icon.className = 'bi bi-eye-slash';
            btn.title = 'Hide password';
        } else {
            icon.className = 'bi bi-eye';
            btn.title = 'Show password';
        }
    }
}

function initPasswordSecurity(config) {
    const passInput = typeof config.passwordInput === 'string' 
        ? document.querySelector(config.passwordInput) 
        : config.passwordInput;
    const confirmInput = typeof config.confirmInput === 'string'
        ? document.querySelector(config.confirmInput)
        : config.confirmInput;

    if (!passInput) return;

    const prefix = config.prefix || '';
    const reqLength = document.getElementById(prefix + 'req-length');
    const reqUpper = document.getElementById(prefix + 'req-upper');
    const reqLower = document.getElementById(prefix + 'req-lower');
    const reqNumber = document.getElementById(prefix + 'req-number');
    const reqSpecial = document.getElementById(prefix + 'req-special');
    const reqPersonal = document.getElementById(prefix + 'req-personal');
    const reqCommon = document.getElementById(prefix + 'req-common');

    const strengthBar = document.getElementById(prefix + 'strengthBar');
    const strengthBadge = document.getElementById(prefix + 'strengthBadge');
    const matchFeedback = document.getElementById(prefix + 'matchFeedback');
    const matchText = document.getElementById(prefix + 'matchText');
    const matchIcon = document.getElementById(prefix + 'matchIcon');

    function updateRule(el, isPassing) {
        if (!el) return;
        const icon = el.querySelector('.req-icon');
        if (isPassing) {
            el.classList.remove('text-danger');
            el.classList.add('text-success');
            if (icon) {
                icon.className = 'bi bi-check-circle-fill flex-shrink-0 req-icon text-success';
            }
        } else {
            el.classList.remove('text-success');
            el.classList.add('text-danger');
            if (icon) {
                icon.className = 'bi bi-x-circle-fill flex-shrink-0 req-icon text-danger';
            }
        }
    }

    function checkPersonalSimilarity(val) {
        if (!val || val.length === 0) return false;
        const lowerVal = val.toLowerCase();

        // Extract values from user fields (username, email, names)
        const userInputs = (config.userFields || []).map(sel => {
            const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
            return el ? el.value.trim().toLowerCase() : '';
        }).filter(v => v.length >= 3);

        for (const token of userInputs) {
            // Check direct match or substring
            if (lowerVal === token || lowerVal.includes(token) || (token.length >= 4 && token.includes(lowerVal))) {
                return false;
            }
            // Check email local part before @
            if (token.includes('@')) {
                const localPart = token.split('@')[0];
                if (localPart.length >= 3 && (lowerVal.includes(localPart) || localPart.includes(lowerVal))) {
                    return false;
                }
            }
        }
        return true;
    }

    function checkCommonAndNumeric(val) {
        if (!val || val.length === 0) return false;
        const lower = val.toLowerCase().trim();
        // Check if entirely numeric
        if (/^\d+$/.test(lower)) return false;
        // Check against dictionary of common passwords
        if (COMMON_PASSWORDS.has(lower)) return false;
        // Check repeated sequences like aaaaa or 11111
        if (/^(.)\1+$/.test(lower)) return false;
        return true;
    }

    function evaluatePassword() {
        const val = passInput.value || '';
        const hasText = val.length > 0;

        const passLength = val.length >= 8;
        const passUpper = /[A-Z]/.test(val);
        const passLower = /[a-z]/.test(val);
        const passNumber = /[0-9]/.test(val);
        const passSpecial = /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?~`]/.test(val);
        const passPersonal = hasText ? checkPersonalSimilarity(val) : false;
        const passCommon = hasText ? checkCommonAndNumeric(val) : false;

        updateRule(reqLength, passLength);
        updateRule(reqUpper, passUpper);
        updateRule(reqLower, passLower);
        updateRule(reqNumber, passNumber);
        updateRule(reqSpecial, passSpecial);
        updateRule(reqPersonal, passPersonal);
        updateRule(reqCommon, passCommon);

        // Calculate strength score (0 to 7)
        let score = 0;
        if (passLength) score++;
        if (passUpper) score++;
        if (passLower) score++;
        if (passNumber) score++;
        if (passSpecial) score++;
        if (passPersonal) score++;
        if (passCommon) score++;

        if (strengthBar && strengthBadge) {
            strengthBar.className = 'progress-bar';
            strengthBadge.className = 'badge font-monospace';

            if (!hasText) {
                strengthBar.style.width = '0%';
                strengthBadge.textContent = 'Not Entered';
                strengthBadge.classList.add('bg-secondary');
            } else if (score <= 2) {
                strengthBar.style.width = '25%';
                strengthBar.classList.add('bg-danger');
                strengthBadge.textContent = 'Very Weak';
                strengthBadge.classList.add('bg-danger');
            } else if (score <= 4) {
                strengthBar.style.width = '50%';
                strengthBar.classList.add('bg-warning');
                strengthBadge.textContent = 'Weak';
                strengthBadge.classList.add('bg-warning', 'text-dark');
            } else if (score <= 6) {
                strengthBar.style.width = '75%';
                strengthBar.classList.add('bg-info');
                strengthBadge.textContent = 'Moderate';
                strengthBadge.classList.add('bg-info', 'text-dark');
            } else {
                strengthBar.style.width = '100%';
                strengthBar.classList.add('bg-success');
                strengthBadge.textContent = 'Strong & Secure';
                strengthBadge.classList.add('bg-success');
            }
        }

        evaluateMatch();
    }

    function evaluateMatch() {
        if (!confirmInput || !matchFeedback) return;
        const val1 = passInput.value || '';
        const val2 = confirmInput.value || '';

        if (val2.length === 0) {
            matchFeedback.classList.add('d-none');
            return;
        }

        matchFeedback.classList.remove('d-none');
        if (val1 === val2 && val1.length > 0) {
            if (matchText) {
                matchText.className = 'd-flex align-items-center gap-1.5 text-success fw-semibold';
                matchText.textContent = 'Passwords match perfectly';
            }
            if (matchIcon) {
                matchIcon.className = 'bi bi-check-circle-fill text-success';
            }
        } else {
            if (matchText) {
                matchText.className = 'd-flex align-items-center gap-1.5 text-danger fw-semibold';
                matchText.textContent = 'Passwords do not match';
            }
            if (matchIcon) {
                matchIcon.className = 'bi bi-x-circle-fill text-danger';
            }
        }
    }

    // Attach event listeners
    passInput.addEventListener('input', evaluatePassword);
    passInput.addEventListener('focus', evaluatePassword);

    if (confirmInput) {
        confirmInput.addEventListener('input', evaluateMatch);
        confirmInput.addEventListener('focus', evaluateMatch);
    }

    // Listen to personal info fields changes to re-evaluate similarity
    (config.userFields || []).forEach(sel => {
        const el = typeof sel === 'string' ? document.querySelector(sel) : sel;
        if (el) {
            el.addEventListener('input', evaluatePassword);
        }
    });

    // Initial evaluation
    evaluatePassword();
}

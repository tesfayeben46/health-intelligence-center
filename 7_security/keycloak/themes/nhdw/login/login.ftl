<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password') displayInfo=false; section>
  <#if section = "header">
    <h1>${msg("loginAccountTitle")}</h1>
    <p class="lead">Sign in with your Palladium account.</p>
  <#elseif section = "form">
    <#if realm.password>
      <form id="kc-form-login" action="${url.loginAction}" method="post" onsubmit="login.disabled = true; return true;">
        <div class="field">
          <label for="username">${msg("usernameOrEmail")}</label>
          <div class="control">
            <input tabindex="1" id="username" name="username" value="${(login.username!'')}" type="text"
                   autofocus autocomplete="username" placeholder="you@palladium.io"
                   aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>"/>
          </div>
        </div>

        <div class="field">
          <label for="password">${msg("password")}</label>
          <div class="control">
            <input tabindex="2" id="password" name="password" type="password"
                   autocomplete="current-password" placeholder="••••••••••"
                   aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>"/>
            <button class="eye" type="button" tabindex="-1" aria-label="Show password"
                    onclick="var i=document.getElementById('password');i.type=i.type==='password'?'text':'password';">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
          <#if messagesPerField.existsError('username','password')>
            <span class="field-error">${kcSanitize(messagesPerField.getFirstError('username','password'))?no_esc}</span>
          </#if>
        </div>

        <input type="hidden" id="id-hidden-input" name="credentialId"
               <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if>/>

        <button class="btn-primary" name="login" id="kc-login" tabindex="3" type="submit">${msg("doLogIn")}</button>
      </form>
    </#if>
  <#elseif section = "socialProviders">
    <#list social.providers as p>
      <a class="btn-social" id="social-${p.alias}" href="${p.loginUrl}">
        <span class="social-badge">${p.displayName?substring(0,1)?upper_case}</span>
        <span>Continue with ${p.displayName}</span>
      </a>
    </#list>
  </#if>
</@layout.registrationLayout>

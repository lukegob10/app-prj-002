import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { AdminAccount, ApiService, errorMessage } from '../services/api.service';

@Component({
  selector:'agora-admin-support', standalone:true, imports:[CommonModule],
  template:`
    <section class="admin-panel" aria-labelledby="admin-title">
      <div class="heading"><div><span class="eyebrow">Administrator</span><h2 id="admin-title">Account support</h2><p>Find an account and help its owner regain access.</p></div><button class="refresh" type="button" (click)="load()" [disabled]="loading">Refresh</button></div>
      <p *ngIf="loading" class="muted" role="status">Loading accounts…</p>
      <p *ngIf="error" class="error" role="alert">{{error}}</p>
      <p *ngIf="notice" class="success" role="status">{{notice}}</p>
      <div *ngIf="!loading && accounts.length" class="columns">
        <div class="list-column"><label for="account-search">Find account</label><input id="account-search" type="search" [value]="search" (input)="search=asText($event)" placeholder="Username or name"><div class="account-list" role="list">
          <button *ngFor="let account of filteredAccounts" role="listitem" type="button" [class.selected]="selected?.id===account.id" (click)="select(account)"><strong>{{account.full_name}}</strong><span>{{'@'+account.username}}{{account.is_admin ? ' · Administrator' : ''}}</span></button>
          <p *ngIf="!filteredAccounts.length" class="muted">No matching accounts.</p>
        </div></div>
        <form *ngIf="selected as account" class="reset-form" (submit)="reset($event)"><h3>{{account.full_name}}</h3><p class="muted">{{'@'+account.username}}</p><p class="warning">Verify this person’s identity through your organization’s support process before resetting their password. All existing sessions will end.</p>
          <label for="new-password">New password</label><input id="new-password" type="password" autocomplete="new-password" [value]="newPassword" (input)="newPassword=asText($event)" minlength="12" required placeholder="At least 12 characters"><small>Share the new password through an approved private channel.</small>
          <label class="verify"><input type="checkbox" [checked]="identityVerified" (change)="identityVerified=asChecked($event)"><span>I verified this person’s identity.</span></label>
          <button class="reset-button" type="submit" [disabled]="busy || !identityVerified || newPassword.length<12">{{busy?'Resetting…':'Reset password'}}</button>
        </form>
        <div *ngIf="!selected" class="placeholder">Select an account to view support actions.</div>
      </div>
      <p *ngIf="!loading && !accounts.length && !error" class="muted">No accounts found.</p>
    </section>
  `,
  styles:[`
    .admin-panel{margin:0 0 27px;padding:19px;border:1px solid var(--border);border-radius:11px;background:var(--surface);color:var(--text)}.heading{display:flex;justify-content:space-between;align-items:start;gap:14px;border-bottom:1px solid var(--border);padding-bottom:15px;margin-bottom:16px}.eyebrow{font-size:.67rem;letter-spacing:.11em;text-transform:uppercase;color:var(--accent-text);font-weight:800}h2{font-size:1.06rem;margin:5px 0 3px}h3{font-size:.93rem;margin:0}.heading p,.muted,small{font-size:.76rem;color:var(--text-muted);line-height:1.5;margin:0}.refresh{background:transparent;color:var(--accent-text);border:1px solid var(--border-strong);border-radius:7px;padding:6px 10px;font-size:.75rem}.columns{display:grid;grid-template-columns:minmax(180px,.85fr) minmax(230px,1.15fr);gap:20px}.list-column,.reset-form{min-width:0}.list-column label,.reset-form label:not(.verify){display:block;color:var(--text-muted);font-size:.74rem;font-weight:700;margin:0 0 5px}.list-column>input,.reset-form>input{width:100%;background:var(--input-bg);border:1px solid var(--border-strong);border-radius:7px;color:var(--text);padding:9px;font-size:.8rem}.account-list{display:grid;gap:5px;max-height:230px;overflow:auto;margin-top:9px}.account-list button{text-align:left;border:1px solid transparent;border-radius:7px;padding:9px;background:var(--surface-raised);color:var(--text)}.account-list button:hover,.account-list button.selected{border-color:var(--accent);background:var(--surface-hover)}.account-list strong,.account-list span{display:block;font-size:.76rem}.account-list span{color:var(--text-muted);font-size:.7rem;margin-top:2px}.reset-form{border-left:1px solid var(--border);padding-left:19px}.reset-form .muted{margin:3px 0 10px}.warning{font-size:.74rem;line-height:1.5;color:var(--warning-text);background:var(--warning-bg);border-radius:7px;padding:9px;margin:10px 0 14px}.reset-form small{display:block;margin-top:5px}.verify{display:flex;align-items:start;gap:7px;font-size:.75rem;color:var(--text-muted);margin:14px 0}.verify input{margin:2px 0 0;accent-color:var(--accent)}.reset-button{border:1px solid var(--primary-bg);border-radius:7px;background:var(--primary-bg);color:var(--primary-text);font-weight:800;padding:8px 12px;font-size:.76rem}.reset-button:disabled{opacity:.48;cursor:default}.placeholder{display:grid;place-items:center;color:var(--text-muted);font-size:.78rem;border:1px dashed var(--border);border-radius:7px;padding:18px}.error{background:var(--error-bg);color:var(--error-text);border-radius:7px;padding:9px;font-size:.77rem}.success{background:var(--success-bg);color:var(--success-text);border-radius:7px;padding:9px;font-size:.77rem}@media(max-width:660px){.columns{grid-template-columns:1fr}.reset-form{border-left:0;border-top:1px solid var(--border);padding:15px 0 0}}
  `]
})
export class AdminSupportComponent implements OnInit {
  accounts:AdminAccount[]=[];selected:AdminAccount|null=null;search='';newPassword='';identityVerified=false;loading=false;busy=false;error='';notice='';
  constructor(private readonly api:ApiService,private readonly router:Router){}
  ngOnInit():void{this.load();}
  get filteredAccounts():AdminAccount[]{const term=this.search.trim().toLowerCase();return term?this.accounts.filter(a=>a.username.toLowerCase().includes(term)||a.full_name.toLowerCase().includes(term)):this.accounts;}
  asText(event:Event):string{return (event.target as HTMLInputElement).value;}
  asChecked(event:Event):boolean{return (event.target as HTMLInputElement).checked;}
  select(account:AdminAccount):void{this.selected=account;this.newPassword='';this.identityVerified=false;this.error='';this.notice='';}
  load():void{this.loading=true;this.error='';this.api.adminAccounts().subscribe({next:d=>{this.accounts=d.accounts;this.loading=false;},error:e=>{this.loading=false;this.error=errorMessage(e);}});}
  reset(event:Event):void{event.preventDefault();if(!this.selected||!this.identityVerified||this.newPassword.length<12||this.busy)return;this.busy=true;this.error='';const username=this.selected.username;this.api.resetAccountPassword(username,this.newPassword).subscribe({next:()=>{this.busy=false;this.newPassword='';this.identityVerified=false;this.notice=`Password reset for @${username}. Existing sessions have ended.`;if(username===this.api.currentUser()?.username)this.router.navigateByUrl('/login');},error:e=>{this.busy=false;this.error=errorMessage(e);}});}
}

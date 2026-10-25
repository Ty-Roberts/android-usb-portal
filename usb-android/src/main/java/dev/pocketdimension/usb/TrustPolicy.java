package dev.pocketdimension.usb;

import java.util.*;

/** Phone-authoritative, non-sliding trust. No host-provided time values are accepted. */
public final class TrustPolicy {
    public static final long TRUST_MILLIS = 7L * 24 * 60 * 60 * 1000;
    public interface Clock { long wallMillis(); long elapsedMillis(); }
    public interface Store {
        Map<String, Grant> load();
        long lastSeenWall();
        void save(Map<String, Grant> grants, long lastSeenWall);
    }
    public static final class Grant {
        public final String fingerprint, name;
        public final long approvedAt, expiresAt;
        public Grant(String fingerprint, String name, long approvedAt, long expiresAt) {
            this.fingerprint=fingerprint; this.name=name; this.approvedAt=approvedAt; this.expiresAt=expiresAt;
        }
    }
    private final Clock clock;
    private final Store store;
    private final Map<String, Grant> grants;
    private long highWater, persistedWall;
    public TrustPolicy(Clock clock, Store store) {
        this.clock=clock;this.store=store;this.grants=new HashMap<>(store.load());
        this.highWater=this.persistedWall=store.lastSeenWall();
    }
    private long observe() {
        long now=clock.wallMillis();
        if(now<highWater) {
            grants.clear();highWater=now;persist();
            throw new SecurityException("Device clock moved backward; pair again");
        }
        highWater=now;
        if(now-persistedWall>60000)persist();
        return now;
    }
    private void persist() {store.save(new HashMap<>(grants),highWater);persistedWall=highWater;}
    public synchronized Grant approve(String fingerprint,String name) {
        if(!fingerprint.matches("[0-9a-f]{64}"))throw new IllegalArgumentException("Invalid certificate fingerprint");
        if(name==null||name.trim().isEmpty()||name.length()>80||name.chars().anyMatch(c->c<32||c>126))throw new IllegalArgumentException("Invalid computer name");
        long now=observe();Grant grant=new Grant(fingerprint,name,now,Math.addExact(now,TRUST_MILLIS));
        Map<String,Grant> updated=new HashMap<>(grants);updated.put(fingerprint,grant);
        store.save(updated,highWater);persistedWall=highWater;grants.put(fingerprint,grant);return grant;
    }
    public synchronized Authorization authorize(String fingerprint) {
        long now=observe();Grant grant=grants.get(fingerprint);
        if(grant==null||now>=grant.expiresAt||grant.approvedAt>now||grant.expiresAt-grant.approvedAt!=TRUST_MILLIS) {
            if(grant!=null){grants.remove(fingerprint);persist();}
            return null;
        }
        return new Authorization(grant,Math.addExact(clock.elapsedMillis(),grant.expiresAt-now));
    }
    public synchronized List<Grant> list() {
        long now=observe();boolean removed=grants.values().removeIf(g->g.expiresAt<=now||g.approvedAt>now||g.expiresAt-g.approvedAt!=TRUST_MILLIS);
        if(removed)persist();return new ArrayList<>(grants.values());
    }
    public synchronized void revoke(String fingerprint) {observe();grants.remove(fingerprint);persist();}
    public synchronized void revokeAll() {observe();grants.clear();persist();}
    public final class Authorization {
        public final Grant grant;
        private final long elapsedDeadline;
        private volatile boolean invalid;
        private Authorization(Grant grant,long deadline){this.grant=grant;this.elapsedDeadline=deadline;}
        public void invalidate(){invalid=true;}
        public void requireValid(){
            synchronized(TrustPolicy.this){
                long now=observe();
                if(invalid||grants.get(grant.fingerprint)!=grant||now>=grant.expiresAt||clock.elapsedMillis()>=elapsedDeadline)
                    throw new SecurityException("Computer trust expired or was revoked; pair again");
            }
        }
        public long remainingMillis(){synchronized(TrustPolicy.this){requireValid();return Math.max(0,Math.min(grant.expiresAt-clock.wallMillis(),elapsedDeadline-clock.elapsedMillis()));}}
    }
}

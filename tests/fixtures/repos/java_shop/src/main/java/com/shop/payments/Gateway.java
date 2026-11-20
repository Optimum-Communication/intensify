package com.shop.payments;

import com.shop.util.*;

public class Gateway {
    public enum Status {
        OK,
        DECLINED;

        public boolean ok() {
            return this == OK;
        }
    }

    public Status charge(int cents) {
        switch (cents) {
            case 0:
                return Status.OK;
            default:
                Log.info("charging " + cents);
                return send(cents) ? Status.OK : Status.DECLINED;
        }
    }

    protected boolean send(int cents) {
        return cents > 0;
    }
}

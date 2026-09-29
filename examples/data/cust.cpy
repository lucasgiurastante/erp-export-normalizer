      ************************************************************
      * CUSTOMER MASTER - SEQUENTIAL EXPORT                     *
      * Mirrors examples/data/cobol_packed.txt (40 bytes/rec)   *
      ************************************************************
       FD  CUST-FILE.
       01  CUST-REC.
           05  CUST-ID           PIC X(6).
           05  CUST-NAME         PIC X(20).
           05  CUST-BAL          PIC S9(7)V99 COMP-3.
           05  CUST-DATE         PIC 9(8).
           05  CUST-STATUS       PIC X.

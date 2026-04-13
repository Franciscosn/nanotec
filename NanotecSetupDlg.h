// NanotecSetupDlg.h : header file
//
#if _MSC_VER > 1000
#pragma once
#endif // _MSC_VER > 1000

using namespace std;

#include "ComLib.h"

/////////////////////////////////////////////////////////////////////////////
// CNanotecSetupDlg dialog

class CNanotecSetupDlg : public CPropertyPage
{
	DECLARE_DYNCREATE(CNanotecSetupDlg)

// Construction
public:
	CNanotecSetupDlg();
	~CNanotecSetupDlg();

// Dialog Data
	//{{AFX_DATA(CNanotecSetupDlg)
	enum { IDD = IDD_NANOTEC_SETUP_DLG };
	BOOL	bRS485;
	DWORD	dwRS232TimeOutFactor;
	CString sComPort;
	BOOL	bMonitor;
	DWORD	dwMaxNoOfRetries;

	BOOL	bMotor1, bMotor2, bInverted1, bInverted2;
	DWORD	dwAdr1, dwAdr2;
	DWORD	dwMaxSteps1, dwMaxSteps2;
	//}}AFX_DATA
	ComParameters SetupCom;

// Overrides
	// ClassWizard generate virtual function overrides
	//{{AFX_VIRTUAL(CNanotecSetupDlg)
	public:
	virtual BOOL OnSetActive();
	protected:
	virtual void DoDataExchange(CDataExchange* pDX);    // DDX/DDV support
	//}}AFX_VIRTUAL

// Implementation
protected:
	// Generated message map functions
	//{{AFX_MSG(CNanotecSetupDlg)
	afx_msg void OnRS485SetupDlgRS232();
	afx_msg void OnRS485SetupDlgSetupComPort();
	afx_msg void OnRS485SetupDlgMonitoring();
	afx_msg void OnMotor1();
	afx_msg void OnMotor2();
	afx_msg void OnOK();
	//}}AFX_MSG
	DECLARE_MESSAGE_MAP()

};


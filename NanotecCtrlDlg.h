// CNanotecCtrlDlg.h : header file
//

#if _MSC_VER > 1000
#pragma once
#endif // _MSC_VER > 1000

using namespace std;

#include "Resource.h"
//#include "Coater.h"


/////////////////////////////////////////////////////////////////////////////
// CNanotecCtrlDlg dialog

class CNanotecCtrlDlg : public CDialog
{
// Construction
public:
	CNanotecCtrlDlg(CWnd* pParent = NULL);   // standard constructor

// Dialog Data
	//{{AFX_DATA(CNanotecCtrlDlg)
	enum { IDD = IDD_NANOTEC_CTRL_DLG };
		// NOTE: the ClassWizard will add data members here
	CString sFirmware[2];
	//}}AFX_DATA

// Overrides
	// ClassWizard generated virtual function overrides
	//{{AFX_VIRTUAL(CNanotecCtrlDlg)
	public:
//	virtual BOOL DestroyWindow();
	protected:
	virtual void DoDataExchange(CDataExchange* pDX);    // DDX/DDV support
	//}}AFX_VIRTUAL

// Implementation
protected:

	// Generated message map functions
	//{{AFX_MSG(CNanotecCtrlDlg)
//	virtual BOOL OnInitDialog();
//	afx_msg void OnNhqCtrlDlgSetHV();
//	afx_msg void OnNhqCtrlDlgGetHV();
//	afx_msg void OnNhqCtrlDlgResetNhq();
//	afx_msg void OnNhqCtrlDlgTake();
//	afx_msg HBRUSH OnCtlColor( CDC* pDC, CWnd* pWnd, UINT nCtlColor );
//	afx_msg void OnNhqCtrlDlgShutdown();
//	afx_msg void OnNhqCtrlDlgOk();
//	afx_msg void OnNhqCtrlDlgCancel();
////	afx_msg void OnNhqCtrlDlgChannel0();
////	afx_msg void OnNhqCtrlDlgChannel1();
//	afx_msg void OnTimer(UINT_PTR nIDEvent);
	//}}AFX_MSG
	DECLARE_MESSAGE_MAP()

	/*int NhqCtrlDlgGetHV();
	void SetStatusBar( int Pos, CString Text = "" );*/

private:

};
